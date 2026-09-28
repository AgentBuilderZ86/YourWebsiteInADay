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
        "no_site": "Aucun site web : vos clients ne vous trouvent pas sur Google",
        "down": "Le site est inaccessible (erreur {status})",
        "expired": "Le nom de domaine {host} ne répond plus (probablement expiré) : votre site a disparu d'internet",
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
        "no_site": "No website: customers can't find you on Google",
        "down": "The website is down (error {status})",
        "expired": "The domain {host} no longer resolves (probably expired): your website has vanished from the internet",
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript"):
            self._skip += 1
            if tag == "script" and not a.get("src"):
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
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if not self._skip:
            self.text_parts.append(data)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)


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
        add("stale", 10, year=max(years))

    emails = extract_emails(html)
    score = max(0, 100 - sum(i.penalty for i in issues))
    issues.sort(key=lambda i: -i.penalty)
    return AuditResult(final_url, score, issues, emails, round(load_seconds, 2),
                       content=extract_site_content(html, final_url))


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
    if use_pagespeed and result.verified:
        ps = _pagespeed_mobile_score(resp.url)
        if ps is not None and ps < 50:
            result.issues.append(Issue("pagespeed", 10, {"score": ps}))
            result.score = max(0, result.score - 10)
    return result
