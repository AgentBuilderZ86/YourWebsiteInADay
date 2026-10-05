"""Audit automatique d'un site web : score /100 + liste de problèmes compréhensibles par un commerçant."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import urllib3

from .content import extract_site_content, is_challenge_page, is_parking_page

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Navigateur réaliste : beaucoup de sites renvoient 403 aux robots déclarés
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
HEADERS = {"User-Agent": BROWSER_UA, "Accept-Language": "fr,en;q=0.8"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,24}")
IGNORED_EMAIL_PARTS = ("example.", "exemple.", "sentry", "wixpress", "@2x", "domain.", "domaine.",
                       "yourdomain", "godaddy", "wix.com", "noreply", "no-reply", "@sentry")
IMAGE_TLDS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "avif", "css", "js"}
PLACEHOLDER_LOCALS = {"abc", "votre", "your", "you", "email", "e-mail", "mail", "name", "nom", "prenom", "user",
                      "test", "exemple", "example", "xxx", "john.doe", "jean.dupont", "adresse", "address",
                      "username", "firstname.lastname", "prenom.nom", "vous"}
PLACEHOLDER_DOMAINS = {"xyz.com", "mail.com", "email.com", "email.fr", "monsite.fr", "monsite.com", "site.com",
                       "yoursite.com", "test.com", "company.com", "entreprise.fr", "votresite.fr"}
FREE_MAIL = {"gmail.com", "hotmail.com", "hotmail.fr", "outlook.com", "outlook.fr", "yahoo.com", "yahoo.fr",
             "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net", "icloud.com", "live.com", "live.fr",
             "menara.ma", "gmx.fr", "gmx.com", "bbox.fr", "aol.com", "neuf.fr", "skynet.be", "telenet.be"}
# Sous-domaines gratuits : le commerçant n'a pas son propre nom de domaine
FREE_HOSTS = ("wixsite.com", "business.site", "jimdosite.com", "jimdo.com", "webnode.", "e-monsite.com",
              "site123.me", "wordpress.com", "blogspot.com", "weebly.com", "godaddysites.com", "square.site")
PARKED_MARKERS = (  # site en chantier (le domaine appartient toujours au commerce)
    "under construction", "en construction", "coming soon", "site en maintenance", "bientôt en ligne",
)


@dataclass
class Issue:
    code: str
    penalty: int
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return issue_label(self.code, self.params, "fr")


# Phrases destinées au commerçant, par langue
ISSUE_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "no_site": "Aucun site web trouvé à votre nom",
        "down": "Le site est inaccessible (erreur {status})",
        "expired": "Le nom de domaine {host} ne répond plus (probablement expiré) : votre site a disparu d'internet",
        "listing": "L'adresse {host} n'affiche qu'une liste de fichiers techniques (« Index of / ») : vos clients n'y voient pas votre site",
        "parked": "L'adresse {host} affiche une page d'hébergeur ou de revente de nom de domaine : votre site n'y existe plus",
        "empty": "Le site est vide, en construction ou quasi sans contenu",
        "no_https": "Le site n'est pas sécurisé (pas de HTTPS) : Chrome affiche « Non sécurisé »",
        "bad_ssl": "Le certificat de sécurité est invalide : les visiteurs voient une alerte",
        "not_mobile": "Le site n'est pas adapté aux smartphones (70 % des visites)",
        "very_slow": "Chargement très lent ({seconds} s) : la moitié des visiteurs partent",
        "slow": "Chargement lent ({seconds} s)",
        "heavy": "Page très lourde ({mb} Mo)",
        "no_title": "Pas de titre de page : invisible dans les résultats Google",
        "no_description": "Pas de description Google : le résultat de recherche est peu attractif",
        "no_h1": "Structure de page non optimisée pour le référencement",
        "legacy_tech": "Technologies obsolètes ({tags})",
        "old_jquery": "Bibliothèques techniques anciennes (failles de sécurité possibles)",
        "table_layout": "Mise en page datée (construite en tableaux)",
        "img_alt": "Images non décrites : perte de référencement Google Images",
        "no_og": "Aperçu non optimisé quand le lien est partagé sur WhatsApp / Facebook",
        "no_cta": "Pas de bouton d'appel / WhatsApp : le client ne peut pas vous contacter en 1 clic",
        "stale": "Site non mis à jour depuis {year}",
        "pagespeed": "Score de performance mobile Google : {score}/100",
        "no_domain": "Pas de nom de domaine propre ({host}) : image peu professionnelle",
    },
    "en": {
        "no_site": "No website found under your name",
        "down": "The website is down (error {status})",
        "expired": "The domain {host} no longer resolves (probably expired): your website has vanished from the internet",
        "listing": "{host} only shows a raw file listing (\"Index of /\"): customers don't see your website there",
        "parked": "{host} now shows a hosting or domain-resale page: your website no longer exists there",
        "empty": "The website is empty, under construction or has almost no content",
        "no_https": "The site isn't secure (no HTTPS): Chrome shows \"Not secure\"",
        "bad_ssl": "The security certificate is invalid: visitors get a warning",
        "not_mobile": "The site isn't mobile-friendly (70% of visits are on phones)",
        "very_slow": "Very slow to load ({seconds}s): half of visitors leave",
        "slow": "Slow to load ({seconds}s)",
        "heavy": "Very heavy page ({mb} MB)",
        "no_title": "No page title: poor visibility in Google results",
        "no_description": "No meta description: your Google listing looks unappealing",
        "no_h1": "Page structure not optimised for SEO",
        "legacy_tech": "Outdated technology ({tags})",
        "old_jquery": "Outdated code libraries (possible security holes)",
        "table_layout": "Dated layout (built with tables)",
        "img_alt": "Images without descriptions: lost Google Images traffic",
        "no_og": "No preview when your link is shared on WhatsApp / Facebook",
        "no_cta": "No call / WhatsApp button: customers can't reach you in one tap",
        "stale": "Site not updated since {year}",
        "pagespeed": "Google mobile performance score: {score}/100",
        "no_domain": "No domain name of your own ({host}): looks unprofessional",
    },
}


def issue_label(code: str, params: dict[str, Any], lang: str) -> str:
    labels = ISSUE_LABELS.get(lang, ISSUE_LABELS["fr"])
    if code == "expired" and "host" in params:
        params = {**params, "host": str(params["host"]).removeprefix("www.")}
    try:
        return labels[code].format(**params)
    except (KeyError, IndexError):
        return labels.get(code, code)


@dataclass
class AuditResult:
    url: str | None
    score: int
    issues: list[Issue] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    load_seconds: float | None = None
    reachable: bool = True
    content: dict[str, Any] = field(default_factory=dict)
    # False quand l'audit n'est pas fiable (anti-robot, site en JavaScript…) : on ne prospecte pas
    verified: bool = True
    note: str = ""
    # Contrôles SEO & GEO (visibilité Google et assistants IA) : hors note, pour le rapport d'audit
    geo: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "score": self.score,
            "reachable": self.reachable,
            "verified": self.verified,
            "note": self.note,
            "load_seconds": self.load_seconds,
            "issues": [issue_dict(i) for i in self.issues],
            "emails": self.emails,
        }


def issue_dict(i: Issue) -> dict[str, Any]:
    return {"code": i.code, "penalty": i.penalty, "params": i.params, "label": i.label}


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.h1 = 0
        self.imgs = 0
        self.imgs_no_alt = 0
        self.tables = 0
        self.legacy_tags: set[str] = set()
        self.scripts: list[str] = []
        self.links: list[str] = []
        self.text_parts: list[str] = []
        self.inline_scripts = 0
        self._skip = 0
        self.jsonld: list[str] = []
        self._in_ld = False
        self.lang = ""
        self.canonical = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang", "")
        elif tag == "link" and "canonical" in a.get("rel", "").lower():
            self.canonical = True
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in_ld = True
            self.jsonld.append("")
        if tag in ("script", "style", "noscript"):
            self._skip += 1
            if tag == "script" and not a.get("src") and "ld+json" not in a.get("type", "").lower():
                self.inline_scripts += 1
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name:
                self.meta[name] = a.get("content", "")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img":
            self.imgs += 1
            if not a.get("alt", "").strip():
                self.imgs_no_alt += 1
        elif tag == "table":
            self.tables += 1
        elif tag in ("marquee", "font", "frameset", "frame", "center", "blink"):
            self.legacy_tags.add(tag)
        elif tag in ("embed", "object") and "flash" in (a.get("type", "") + a.get("src", "") + a.get("data", "")).lower():
            self.legacy_tags.add("flash")
        elif tag == "script" and a.get("src"):
            self.scripts.append(a["src"])
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_ld = False
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in_ld and self.jsonld:
            self.jsonld[-1] += data
        if self._in_title:
            self.title += data
        if not self._skip:
            self.text_parts.append(data)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)


# (problème constaté, recommandation) par contrôle SEO/GEO
GEO_LABELS = {
    "fr": {
        "schema_local": ("Aucune donnée structurée « commerce local » (schema.org)",
                         "Ajouter un bloc schema.org LocalBusiness (nom, adresse, téléphone, horaires, avis) : c'est ce que lisent Google et les IA pour vous identifier."),
        "schema_faq": ("Pas de FAQ structurée",
                       "Publier une FAQ (prix, accès, réservation…) balisée FAQPage : les assistants IA reprennent directement ces réponses."),
        "nap": ("Nom, adresse et téléphone pas clairement lisibles sur la page",
                "Afficher le trio nom-adresse-téléphone en texte, identique à votre fiche Google : c'est le premier signal de confiance local."),
        "hours": ("Horaires absents de la page", "Publier vos horaires en texte et en données structurées (openingHours)."),
        "meta_description": ("Pas de description pour Google", "Rédiger une méta-description de 150 caractères avec votre métier et votre ville."),
        "title": ("Titre de page absent, trop court ou trop long", "Titre de 50 à 60 caractères : « Métier à Ville — Nom »."),
        "h1": ("Pas de titre principal (H1)", "Un titre H1 unique qui dit clairement ce que vous faites et où."),
        "lang": ("Langue de la page non déclarée", "Déclarer la langue (lang=\"fr\") pour être servi aux bonnes recherches."),
        "canonical": ("Pas d'URL canonique", "Déclarer l'URL canonique pour éviter les doublons dans Google."),
        "og": ("Aperçu de partage absent (réseaux sociaux, messageries)", "Ajouter titre et image Open Graph : vos liens partagés sur WhatsApp ou Facebook deviennent des vitrines."),
        "content_depth": ("Contenu trop mince pour être compris par Google et les IA",
                          "Au moins 300 mots utiles : services, spécialités, quartier, questions fréquentes."),
        "ai_crawlers": ("Les robots des assistants IA sont bloqués ({blocked})",
                        "Autoriser GPTBot, ClaudeBot, PerplexityBot et Google-Extended dans robots.txt pour pouvoir être cité par les IA."),
        "sitemap": ("Pas de plan du site (sitemap.xml)", "Publier un sitemap.xml et le déclarer à Google Search Console."),
        "llms_txt": ("Pas de fichier llms.txt", "Ajouter un llms.txt qui résume votre activité pour les assistants IA (nouveau standard GEO)."),
        "site_down": ("Votre site ne s'affiche plus : Google et les assistants IA n'ont plus de source officielle sur vous",
                      "Remettre en ligne un site rapide, sécurisé et structuré : c'est la condition pour réapparaître dans Google et dans les réponses des IA."),
        "gbp": ("Soigner votre fiche Google Business Profile et la relier au site",
                "Horaires, photos, catégorie précise, lien vers le site : c'est la source n°1 de Google Maps et de ses réponses IA."),
        "consistency": ("Harmoniser vos coordonnées partout",
                        "Même nom, adresse et téléphone sur Google, Facebook, TripAdvisor et annuaires : les IA recoupent ces sources avant de citer un commerce."),
        "faq_plan": ("FAQ et données structurées à créer",
                     "Répondre aux questions que vos clients posent (prix, accès, réservation) en format FAQ balisé : les IA reprennent ces réponses mot pour mot."),
        "reviews": ("Mettre en avant vos avis clients", "Afficher et baliser vos avis sur le site : un signal de confiance fort pour Google comme pour les IA."),
        "no_site": ("Aucun site trouvé : ni Google ni les assistants IA n'ont de source officielle sur vous",
                    "Un site rapide avec données structurées, FAQ et fiche Google reliée : la base pour apparaître dans les réponses de Google et de ChatGPT."),
    },
    "en": {
        "schema_local": ("No local-business structured data (schema.org)",
                         "Add a schema.org LocalBusiness block (name, address, phone, hours, reviews): it's what Google and AI assistants read to identify you."),
        "schema_faq": ("No structured FAQ", "Publish an FAQ (prices, access, booking…) marked up as FAQPage: AI assistants quote these answers directly."),
        "nap": ("Name, address and phone not clearly readable on the page",
                "Show name-address-phone as text, identical to your Google profile: the first local trust signal."),
        "hours": ("Opening hours missing from the page", "Publish your hours as text and structured data (openingHours)."),
        "meta_description": ("No description for Google", "Write a 150-character meta description with your trade and city."),
        "title": ("Page title missing, too short or too long", "A 50–60 character title: \"Trade in City — Name\"."),
        "h1": ("No main heading (H1)", "One H1 that clearly says what you do and where."),
        "lang": ("Page language not declared", "Declare the language (lang attribute) to be served to the right searches."),
        "canonical": ("No canonical URL", "Declare the canonical URL to avoid duplicates in Google."),
        "og": ("No sharing preview (social, messaging apps)", "Add Open Graph title and image: links shared on WhatsApp or Facebook become shop windows."),
        "content_depth": ("Content too thin for Google and AI to understand", "At least 300 useful words: services, specialities, area, FAQs."),
        "ai_crawlers": ("AI assistants' crawlers are blocked ({blocked})",
                        "Allow GPTBot, ClaudeBot, PerplexityBot and Google-Extended in robots.txt so AI assistants can cite you."),
        "sitemap": ("No sitemap.xml", "Publish a sitemap.xml and submit it in Google Search Console."),
        "llms_txt": ("No llms.txt file", "Add an llms.txt summarising your business for AI assistants (emerging GEO standard)."),
        "site_down": ("Your website no longer loads: Google and AI assistants have lost their official source about you",
                      "Bring back a fast, secure, structured website: the prerequisite to reappear in Google and in AI answers."),
        "gbp": ("Polish your Google Business Profile and link it to the site",
                "Hours, photos, precise category, link to the site: the #1 source for Google Maps and its AI answers."),
        "consistency": ("Align your contact details everywhere",
                        "Same name, address and phone on Google, Facebook, TripAdvisor and directories: AI assistants cross-check these sources before citing a business."),
        "faq_plan": ("FAQ and structured data to create",
                     "Answer your customers' questions (prices, access, booking) in marked-up FAQ format: AI assistants quote these answers word for word."),
        "reviews": ("Showcase your customer reviews", "Show and mark up your reviews on the site: a strong trust signal for Google and AI alike."),
        "no_site": ("No website found: neither Google nor AI assistants have an official source about you",
                    "A fast site with structured data, FAQ and a linked Google profile: the foundation to appear in Google's and ChatGPT's answers."),
    },
}


def geo_findings(geo: list[dict[str, Any]] | None, lang: str, has_site: bool = True,
                 site_down: bool = False) -> list[dict[str, str]]:
    """Points à corriger (problème + recommandation), dans l'ordre d'impact."""
    labels = GEO_LABELS.get(lang, GEO_LABELS["fr"])
    if not has_site:
        first = "site_down" if site_down else "no_site"
        return [{"code": c, "problem": labels[c][0], "fix": labels[c][1]}
                for c in (first, "gbp", "consistency", "faq_plan", "reviews")]
    order = ["ai_crawlers", "schema_local", "nap", "hours", "schema_faq", "content_depth", "meta_description",
             "title", "h1", "llms_txt", "sitemap", "og", "lang", "canonical"]
    failing = {g["code"]: g for g in geo or [] if not g.get("ok")}
    out = []
    for code in order:
        if code in failing:
            prob, rec = labels[code]
            blocked = ", ".join(failing[code].get("blocked", []))
            out.append({"code": code, "problem": prob.format(blocked=blocked), "fix": rec})
    return out


def geo_score(geo: list[dict[str, Any]] | None) -> int | None:
    if not geo:
        return None
    return round(100 * sum(1 for g in geo if g.get("ok")) / len(geo))


LOCAL_TYPES = ("localbusiness", "restaurant", "hotel", "lodgingbusiness", "store", "beautysalon", "hairsalon",
               "dentist", "medicalbusiness", "foodestablishment", "cafeorcoffeeshop", "bakery", "realestateagent",
               "autorepair", "florist", "optician", "barorpub", "healthandbeautybusiness", "professionalservice")


def geo_checks(p: "_PageParser", html: str) -> list[dict[str, Any]]:
    """Ce qui aide Google ET les assistants IA (ChatGPT, Perplexity, Gemini…) à comprendre et citer
    le commerce. Chaque contrôle est un fait vérifié dans la page, jamais une supposition."""
    ld = " ".join(p.jsonld).lower()
    text = " ".join(p.text.split())
    low = text.lower() + " " + ld
    has_phone = any(h.startswith("tel:") for h in p.links) or bool(re.search(
        r"(?:\+\d{2,3}|\b0)\s?[1-9](?:[\s.-]?\d{2}){4}|\(?\b[2-9]\d{2}\)?[\s.-]\d{3}[\s.-]\d{4}\b", text))
    # Code postal canadien (A1A 1A1), ou code postal FR/MA/US (4-5 chiffres) plus un mot de voie.
    has_address = bool(re.search(r"\b[A-Z]\d[A-Z] ?\d[A-Z]\d\b", text)) or bool(re.search(r"\b\d{4,5}\b", text)) and bool(re.search(
        r"\b(rue|avenue|av\.|ave|boulevard|bd|blvd|place|quai|chemin|route|allée|street|st\.|road|rd|drive|dr\.|way|lane"
        r"|derb|lot|résidence)\b", low))
    checks = [
        ("schema_local", any(t in ld for t in LOCAL_TYPES)),
        ("schema_faq", "faqpage" in ld),
        ("nap", has_phone and has_address),
        ("hours", "openinghours" in ld or bool(re.search(r"horaires|opening hours|trading hours|business hours|\bhours\b|\bopen (?:daily|7 days|every day)|ouvert|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\bdaily\b|\b(?:mon|tue|wed|thu|fri|sat|sun)\s*[-–:]|\b\d{1,2}h(?:\d{2})?\b|\b\d{1,2}(?:[.:]\d{2})?\s*(?:am|pm)\b", low))),
        ("meta_description", bool(p.meta.get("description", "").strip())),
        ("title", 10 <= len(p.title.strip()) <= 70),
        ("h1", p.h1 >= 1),
        ("lang", bool(p.lang)),
        ("canonical", p.canonical),
        ("og", "og:title" in p.meta or "og:image" in p.meta),
        ("content_depth", len(text.split()) >= 300),
    ]
    return [{"code": c, "ok": bool(ok)} for c, ok in checks]


def geo_site_checks(base_url: str, timeout: int = 8) -> list[dict[str, Any]]:
    """Contrôles au niveau du domaine : robots.txt (robots IA bloqués ?), sitemap.xml, llms.txt."""
    root = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    out: list[dict[str, Any]] = []

    def get(path: str) -> requests.Response | None:
        try:
            r = requests.get(root + path, headers=HEADERS, timeout=timeout, allow_redirects=True)
            return r if r.status_code == 200 and "<html" not in r.text[:500].lower() else None
        except requests.RequestException:
            return None

    robots = get("/robots.txt")
    blocked = []
    if robots is not None:
        agent, rules = None, {}
        for line in robots.text.splitlines():
            line = line.split("#")[0].strip()
            if ":" not in line:
                continue
            k, v = (x.strip() for x in line.split(":", 1))
            if k.lower() == "user-agent":
                agent = v.lower()
            elif k.lower() == "disallow" and agent is not None and v == "/":
                rules[agent] = True
        for bot in ("gptbot", "claudebot", "perplexitybot", "google-extended", "ccbot"):
            if rules.get(bot) or rules.get("*"):
                blocked.append(bot)
    out.append({"code": "ai_crawlers", "ok": not blocked, "blocked": blocked})
    out.append({"code": "sitemap", "ok": get("/sitemap.xml") is not None or get("/sitemap_index.xml") is not None})
    out.append({"code": "llms_txt", "ok": get("/llms.txt") is not None})
    return out


def analyze_html(html: str, *, final_url: str, load_seconds: float, page_bytes: int,
                 https_ok: bool, ssl_valid: bool, today: date | None = None) -> AuditResult:
    """Analyse pure (sans réseau) — testable unitairement."""
    today = today or date.today()
    p = _PageParser()
    try:
        p.feed(html)
    except Exception:  # HTML très cassé
        pass
    text_lower = p.text.lower()
    issues: list[Issue] = []

    def add(code: str, penalty: int, **params: Any) -> None:
        issues.append(Issue(code, penalty, params))

    visible = len(" ".join(p.text.split()))
    host = urlparse(final_url).netloc.lower()
    if is_challenge_page(html):
        return AuditResult(final_url, 100, [], [], round(load_seconds, 2), verified=False,
                           note="page anti-robot (captcha), audit non fiable")
    if p.title.strip().lower().startswith("index of /"):
        # Listing de répertoire du serveur : aucun site visible pour le client
        return AuditResult(final_url, 0, [Issue("listing", 100, {"host": host.removeprefix("www.")})],
                           [], round(load_seconds, 2), reachable=False)
    if is_parking_page(p.title, " ".join(p.text.split()), host):
        # Page d'hébergeur, de revente ou d'enchères : le site du commerce n'existe plus à cette adresse
        return AuditResult(final_url, 0, [Issue("parked", 100, {"host": host.removeprefix("www.")})],
                           [], round(load_seconds, 2), reachable=False)
    if any(m in text_lower for m in PARKED_MARKERS) or (visible < 200 and not p.scripts and not p.inline_scripts):
        add("empty", 30)
    elif visible < 200:
        # Contenu généré en JavaScript : notre lecture du HTML n'est pas représentative
        return AuditResult(final_url, 100, [], extract_emails(html), round(load_seconds, 2),
                           verified=False, note="site rendu en JavaScript, audit non fiable")
    if any(h in host for h in FREE_HOSTS):
        add("no_domain", 15, host=host)
    if not https_ok:
        add("no_https", 20)
    elif not ssl_valid:
        add("bad_ssl", 20)
    if "viewport" not in p.meta:
        add("not_mobile", 20)
    # Seuils volontairement hauts : la mesure inclut la latence de notre réseau
    if load_seconds > 8:
        add("very_slow", 15, seconds=round(load_seconds, 1))
    elif load_seconds > 5:
        add("slow", 8, seconds=round(load_seconds, 1))
    if page_bytes > 3_000_000:
        add("heavy", 5, mb=round(page_bytes / 1e6, 1))
    if not p.title.strip():
        add("no_title", 6)
    if not p.meta.get("description", "").strip():
        add("no_description", 5)
    if p.h1 == 0:
        add("no_h1", 3)
    if p.legacy_tags:
        add("legacy_tech", 10, tags=", ".join(sorted(p.legacy_tags)))
    if any(re.search(r"jquery[-.]?1\.\d", s, re.I) for s in p.scripts):
        add("old_jquery", 5)
    if p.tables > 5:
        add("table_layout", 5)
    if p.imgs and p.imgs_no_alt / p.imgs > 0.5:
        add("img_alt", 3)
    if "og:title" not in p.meta and "og:image" not in p.meta:
        add("no_og", 3)
    if not any(h.startswith(("tel:", "mailto:")) or "wa.me" in h or "whatsapp" in h for h in p.links):
        add("no_cta", 5)
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", text_lower)]
    if years and max(years) <= today.year - 3:
        # « © 2017 » en pied de page : plus c'est ancien, plus le site est probablement abandonné
        add("stale", 20 if max(years) <= today.year - 5 else 10, year=max(years))

    emails = extract_emails(html)
    score = max(0, 100 - sum(i.penalty for i in issues))
    issues.sort(key=lambda i: -i.penalty)
    return AuditResult(final_url, score, issues, emails, round(load_seconds, 2),
                       content=extract_site_content(html, final_url), geo=geo_checks(p, html))


CONTACT_HINTS = ("contact", "nous-contacter", "contactez", "about", "a-propos", "mentions-legales",
                 "legal", "impressum", "reservation", "booking")


def plausible_email(email: str) -> bool:
    email = email.lower()
    local, _, domain = email.partition("@")
    if not local or not domain or domain.rsplit(".", 1)[-1] in IMAGE_TLDS:
        return False
    if local in PLACEHOLDER_LOCALS or domain in PLACEHOLDER_DOMAINS:
        return False
    if any(x in email for x in IGNORED_EMAIL_PARTS):
        return False
    # Identifiants aléatoires (noms de fichiers, tokens) : ex. 7p46ot4avp90@1280x853
    if re.fullmatch(r"[a-z0-9]{10,}", local) and sum(c.isdigit() for c in local) >= 3:
        return False
    return True


def extract_emails(html: str) -> list[str]:
    html = html.replace("[at]", "@").replace("(at)", "@").replace("&#64;", "@").replace("%40", "@")
    return sorted({e.lower().rstrip(".") for e in EMAIL_RE.findall(html) if plausible_email(e)})


_mx_cache: dict[str, bool] = {}


def has_mx(domain: str) -> bool:
    """Le domaine reçoit-il des emails ? (DNS-over-HTTPS, pas de dépendance DNS locale)"""
    domain = domain.lower()
    if domain in FREE_MAIL:
        return True
    if domain not in _mx_cache:
        try:
            r = requests.get("https://cloudflare-dns.com/dns-query", params={"name": domain, "type": "MX"},
                             headers={"accept": "application/dns-json"}, timeout=10)
            data = r.json()
            _mx_cache[domain] = data.get("Status") == 0 and any(a.get("type") == 15 for a in data.get("Answer", []))
        except (requests.RequestException, ValueError):
            return True  # en cas de doute réseau, on ne rejette pas
    return _mx_cache[domain]


def domain_exists(domain: str) -> bool | None:
    """True/False si le DNS répond clairement, None en cas de doute."""
    try:
        r = requests.get("https://cloudflare-dns.com/dns-query", params={"name": domain, "type": "A"},
                         headers={"accept": "application/dns-json"}, timeout=10)
        status = r.json().get("Status")
    except (requests.RequestException, ValueError):
        return None
    return {0: True, 3: False}.get(status)


def best_emails(emails: list[str], site_url: str | None) -> list[str]:
    """Emails valides, du plus pertinent au moins pertinent : domaine du site, puis messageries grand public."""
    site = (urlparse(site_url).netloc.lower().removeprefix("www.") if site_url else "")

    def rank(e: str) -> int:
        d = e.split("@")[1]
        if site and (d == site or site.endswith("." + d) or d.endswith("." + site)):
            return 0
        return 1 if d in FREE_MAIL else 2

    ordered = sorted({e.lower() for e in emails if plausible_email(e)}, key=lambda e: (rank(e), e))
    return [e for e in ordered if has_mx(e.split("@")[1])][:3]


def find_contact_emails(base_url: str, html: str, *, timeout: int) -> list[str]:
    """Cherche un email sur les pages contact / mentions légales liées depuis l'accueil."""
    p = _PageParser()
    try:
        p.feed(html)
    except Exception:
        return []
    host = urlparse(base_url).netloc
    candidates: list[str] = []
    for href in p.links:
        url = urljoin(base_url + "/", href)
        if urlparse(url).netloc == host and any(h in url.lower() for h in CONTACT_HINTS) and url not in candidates:
            candidates.append(url)
    for url in candidates[:3]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
        except requests.RequestException:
            continue
        emails = extract_emails(r.text)
        if emails:
            return emails
    return []


def _pagespeed_mobile_score(url: str) -> int | None:
    key = os.environ.get("PAGESPEED_API_KEY")
    if not key:
        return None
    try:
        r = requests.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params={"url": url, "strategy": "mobile", "key": key, "category": "performance"},
            timeout=90,
        )
        r.raise_for_status()
        return round(r.json()["lighthouseResult"]["categories"]["performance"]["score"] * 100)
    except Exception:
        return None


# Codes HTTP qui prouvent une panne (après une 2e tentative) ; les autres (401, 403, 429, 503…)
# viennent souvent d'un pare-feu anti-robot : on ne conclut rien.
DOWN_STATUSES = {404, 410, 500, 502, 504, 521, 522, 523}


def _fetch(url: str, timeout: int, verify: bool = True) -> requests.Response:
    return requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True, verify=verify)


def audit_url(url: str | None, *, timeout: int = 15, use_pagespeed: bool = False) -> AuditResult:
    if not url:
        return AuditResult(None, 0, [Issue("no_site", 100)], reachable=False)
    https_url = re.sub(r"^http://", "https://", url)
    ssl_valid = True
    start = time.monotonic()
    resp = None
    try:
        resp = _fetch(https_url, timeout)
    except requests.exceptions.SSLError:
        ssl_valid = False
        try:
            resp = _fetch(https_url, timeout, verify=False)  # noqa: S501
        except requests.RequestException:
            resp = None
    except requests.RequestException:
        resp = None
    if resp is None:
        # Pas de HTTPS du tout : on retente en HTTP
        try:
            start = time.monotonic()
            resp = _fetch(re.sub(r"^https://", "http://", url), timeout)
        except requests.RequestException as exc:
            host = urlparse(url).netloc.lower()
            bare = host.removeprefix("www.")
            if domain_exists(bare) is False:
                # Le nom de domaine lui-même n'existe plus : expiré
                return AuditResult(url, 0, [Issue("expired", 100, {"host": bare})], reachable=False)
            if domain_exists(host) is False:
                # Le domaine existe (souvent avec sa messagerie) mais aucun site n'y est hébergé
                return AuditResult(url, 0, [Issue("down", 100, {"status": "DNS"})], reachable=False)
            return AuditResult(url, 100, reachable=False, verified=False, note=f"injoignable depuis notre réseau ({exc.__class__.__name__})")
    elapsed = time.monotonic() - start
    if resp.status_code >= 400:
        if resp.status_code in DOWN_STATUSES:
            time.sleep(2)
            try:
                retry = _fetch(url, timeout, verify=False)  # noqa: S501
            except requests.RequestException:
                retry = resp
            if retry.status_code in DOWN_STATUSES:
                return AuditResult(url, 0, [Issue("down", 100, {"status": retry.status_code})], reachable=False)
            resp = retry
        if resp.status_code >= 400:
            return AuditResult(url, 100, reachable=False, verified=False, note=f"HTTP {resp.status_code} (anti-robot probable)")
    result = analyze_html(
        resp.text,
        final_url=resp.url,
        load_seconds=elapsed,
        page_bytes=len(resp.content),
        https_ok=resp.url.startswith("https://"),
        ssl_valid=ssl_valid,
    )
    if not result.emails:
        result.emails = find_contact_emails(resp.url, resp.text, timeout=timeout)
    result.emails = best_emails(result.emails, resp.url)
    if result.verified and result.reachable:
        result.geo += geo_site_checks(resp.url)
    if use_pagespeed and result.verified:
        ps = _pagespeed_mobile_score(resp.url)
        if ps is not None and ps < 50:
            result.issues.append(Issue("pagespeed", 10, {"score": ps}))
            result.score = max(0, result.score - 10)
    return result


# --- Site « caché » : commerce sans site déclaré, mais un domaine à son nom existe -----------------

CONSTRUCTION_MARKERS = ("coming soon", "under construction", "en construction", "prochainement disponible",
                        "site en cours de", "bientôt disponible", "bientot disponible", "maintenance mode")


def _fold(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return " ".join(re.findall(r"[a-z0-9]+", text.replace("'", " ").replace("’", " ")))


MARKET_TLDS = {"MA": ("ma",), "BE": ("be",), "AE": ("ae",), "AU": ("com.au",), "NZ": ("co.nz", "nz"), "CA": ("ca",), "QC": ("ca",)}


def own_site_candidates(name: str, email: str | None, city: str | None = None, market: str | None = None) -> list[str]:
    """Domaines plausibles : nom (collé, avec tirets, sans article), partie locale de l'email, ± « lyon »…"""
    words = _fold(name).split()
    core = [w for w in words if w not in ("le", "la", "les", "l", "chez", "de", "du", "des", "et")] or words
    local = (email or "").split("@")[0].lower()
    bases = {"".join(words), "-".join(words), "".join(core), "-".join(core),
             re.sub(r"[^a-z0-9]", "", local), re.sub(r"[^a-z0-9-]", "", local.replace(".", "-").replace("_", "-"))}
    bases = {b.strip("-") for b in bases if len(b.strip("-")) >= 4}
    c = "".join(_fold(city or "").split()[:1])
    if c:
        bases |= {f"{b}-{c}" for b in bases} | {f"{b}{c}" for b in bases if "-" not in b}
    tlds = ("fr", "com") + MARKET_TLDS.get((market or "").upper(), ())
    return sorted({f"{b}.{tld}" for b in bases for tld in tlds})


def find_own_site(name: str, email: str | None, city: str | None, *, market: str | None = None, timeout: int = 10) -> tuple[str | None, str | None]:
    """Cherche un site à son nom. Retourne (url, None) si trouvé, (url, "construction") si le domaine
    affiche une page « en construction », (None, None) sinon. Un site n'est retenu que si sa page cite le
    nom du commerce ET sa ville : un homonyme ailleurs (terramia.com, marathon à New York) est ignoré."""
    words = _fold(name).split()
    core = " ".join(w for w in words if w not in ("le", "la", "les", "l", "chez")) or " ".join(words)
    city_f = _fold(city or "")
    for domain in own_site_candidates(name, email, city, market):
        if domain_exists(domain) is not True:
            continue
        try:
            r = requests.get(f"https://{domain}", headers=HEADERS, timeout=timeout, allow_redirects=True)
        except requests.RequestException:
            continue
        if r.status_code >= 400:
            continue
        html = r.text or ""
        title = (re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I) or [None, ""])[1]
        text = " ".join(re.sub(r"<script.*?</script>|<style.*?</style>|<[^>]+>", " ", html, flags=re.S | re.I).split())
        if is_parking_page(title, text, domain):
            continue
        page = _fold(title + " " + text[:20000])
        tiny = _fold(title + " " + text[:600])
        if len(text) < 600 and any(m in tiny for m in (_fold(x) for x in CONSTRUCTION_MARKERS)):
            if core.replace(" ", "") in domain.replace("-", ""):
                return f"https://{domain}", "construction"
            continue
        full_ns, core_ns = "".join(words), core.replace(" ", "")
        title_ns, page_ns = _fold(title).replace(" ", ""), page.replace(" ", "")
        named = (full_ns in title_ns                                   # « Le Commerce – Bistro » dans le titre
                 or (" " in core and core in page)                     # nom de plusieurs mots cité dans la page
                 or (core_ns == full_ns and core_ns in page_ns))       # nom d'un mot sans article (« B A R O C O »)
        if named and (not city_f or city_f in page):
            return f"https://{domain}", None
    return None, None
