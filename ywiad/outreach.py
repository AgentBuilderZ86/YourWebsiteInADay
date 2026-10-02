"""Rédaction des messages de prospection (FR / EN) et gestion de la séquence de relances."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import re
from urllib.parse import urlparse

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup, escape

from .discover import social_platform
from .audit import geo_findings, geo_score, issue_label
from .pricing import agency_benchmark, format_price, geo_offers, market, pricing_table

_env = Environment(loader=PackageLoader("ywiad", "templates"), autoescape=False,
                   trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
_html_env = Environment(loader=PackageLoader("ywiad", "templates"), autoescape=select_autoescape(["html"]),
                        trim_blocks=True, lstrip_blocks=True)

# Charte des emails HTML : papier chaud, encre, un accent indigo
COLORS = {"bg": "#f3efe9", "card": "#ffffff", "soft": "#f7f4ef", "ink": "#1a1614", "muted": "#6b625c",
          "faint": "#9a918a", "line": "#e8e2da", "accent": "#4338ca", "danger": "#c92a2a", "warn": "#e67700"}
FONTS = {"serif": "Georgia, 'Times New Roman', serif",
         "sans": "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"}

STRINGS = {
    "fr": {
        "hello": "Bonjour,",
        "tagline": "Studio web · sites livrés en 24 h",
        "intro_site": "J'ai découvert le site {de_name} en cherchant {un_cat} à {city}. Je conçois des sites pour les commerces indépendants, alors j'ai pris quelques minutes pour l'analyser : voici ce qu'un client voit en arrivant — et ce qui le fait souvent repartir.",
        "intro_down": "En cherchant {un_cat} à {city}, j'ai voulu consulter le site {de_name} ({host})… mais il ne s'affiche plus. Chaque client qui tombe sur une erreur part chez un concurrent, et Google finit par retirer le site de ses résultats.",
        "intro_social": "En cherchant {un_cat} à {city}, j'ai trouvé {name} sur {platform}, mais aucun site à votre nom. Une page {platform} ne remplace pas un site : elle remonte mal quand un client tape « {cat} {city} » sur Google, et vous n'en maîtrisez ni la présentation ni les règles.",
        "intro_none": "En cherchant {un_cat} à {city}, j'ai trouvé {name}, mais je n'ai trouvé aucun site web à votre nom. Aujourd'hui, la plupart des clients vérifient horaires, adresse et avis en ligne avant de se déplacer — sans site, ils choisissent souvent un concurrent.",
        "audit_label": "Audit express",
        "verdicts": ("Critique", "À refaire", "À moderniser"),
        "problem": {"social_only": "Pas de site à votre nom", "no_site": "Aucun site web trouvé", "expired": "Votre site a disparu d'internet",
                    "parked": "Votre site a disparu d'internet", "listing": "Votre site ne s'affiche plus", "down": "Votre site ne s'affiche plus"},
        "geo_kicker": "Audit SEO & GEO offert",
        "geo_title": "Votre visibilité sur Google et dans les IA",
        "geo_site": "Votre site obtient {g}/100 sur nos critères SEO & GEO : ce qui permet à Google et aux assistants IA (ChatGPT, Perplexity, Gemini) de comprendre votre commerce et de le recommander. Le rapport détaille {k} recommandations, toutes mises en place dans la refonte.",
        "geo_none": "Le GEO est le nouveau SEO : de plus en plus de clients demandent directement à une IA où aller. Je vous ai préparé {k} recommandations pour apparaître dans ses réponses comme dans Google, toutes mises en place avec votre site.",
        "geo_cta": "Lire mon audit",
        "mockup_kicker": "Votre maquette",
        "mockup_title": "Votre nouveau site est déjà prêt",
        "mockup_text": "J'ai conçu gratuitement une première version du site {de_name} : pensée pour le mobile, rapide, avec prise de contact et itinéraire en un geste. Elle est en ligne, prête à être personnalisée.",
        "mockup_cta": "Voir la maquette",
        "preview_alt": "Aperçu du futur site de",
        "plans_title": "Nos formules",
        "plans_text": "Le travail d'une agence web, livré en 24 h à 7 jours au lieu de plusieurs semaines. Clé en main : design, textes, mise en ligne, hébergement et HTTPS compris.",
        "agency": "En agence : {range}",
        "agency_delay": "{delay}",
        "saving": "≈ {pct} % de moins",
        "value_line": "Standard et Premium incluent l'audit SEO & GEO complet et sa mise en œuvre, facturé {audit} en agence à lui seul.",
        "geo_credit": "Si vous refaites ensuite votre site avec nous, le prix de l'audit est intégralement déduit (commande sous {days} jours).",
        "recommended": "Conseillé pour vous",
        "delivery": "livré en",
        "closing": "Si le rendu vous plaît, répondez simplement « OUI » à cet email : je l'adapte à votre activité et je le mets en ligne à votre nom. Vous ne réglez qu'une fois le site en ligne et validé par vous.",
        "preheader_site": "Votre site obtient {score}/100 — une maquette de votre nouveau site vous attend.",
        "preheader_none": "Une maquette de votre futur site vous attend, gratuitement.",
        "social_text": "En ligne, je n'ai trouvé que votre page {platform} : pas d'adresse à votre nom à donner, pas de page qui ressorte sur Google, et des clients qui ne voient ni vos services ni un moyen simple de réserver.",
        "no_site_phrase": "Je n'ai pas trouvé de site web pour {name}.",
        "social_phrase": "Je n'ai trouvé que la page {platform} de {name}, pas de site à son nom.",
        "no_site_text": "Sans site, un client qui vous cherche sur Google risque de ne trouver ni vos horaires, ni vos services, ni un moyen simple de vous contacter ou de réserver.",
    },
    "en": {
        "hello": "Hi,",
        "tagline": "Web studio · websites live in 24 hours",
        "intro_site": "I came across {name}'s website while looking for {a_cat} in {city}. I design websites for independent businesses, so I took a few minutes to review it: here's what a customer sees when they land — and what often makes them leave.",
        "intro_down": "While looking for {a_cat} in {city}, I tried to visit {name}'s website ({host})… but it no longer loads. Every customer who hits an error goes to a competitor, and Google eventually drops the site from its results.",
        "intro_social": "While looking for {a_cat} in {city}, I found {name} on {platform}, but couldn't find a website of your own. A {platform} page is no substitute for a website: it ranks poorly when customers search \"{cat} {city}\" on Google, and you control neither its look nor its rules.",
        "intro_none": "While looking for {a_cat} in {city}, I found {name} but couldn't find a website for you. Most customers now check opening hours, location and reviews online before visiting — without a site, many pick a competitor.",
        "audit_label": "Quick audit",
        "verdicts": ("Critical", "Needs a rebuild", "Needs updating"),
        "problem": {"social_only": "No website of your own", "no_site": "No website found", "expired": "Your website has vanished",
                    "parked": "Your website has vanished", "listing": "Your website no longer loads", "down": "Your website no longer loads"},
        "geo_kicker": "Free SEO & GEO audit",
        "geo_title": "Your visibility on Google and in AI assistants",
        "geo_site": "Your website scores {g}/100 on our SEO & GEO criteria: what lets Google and AI assistants (ChatGPT, Perplexity, Gemini) understand your business and recommend it. The report details {k} recommendations, all implemented in the redesign.",
        "geo_none": "GEO is the new SEO: more and more customers ask an AI directly where to go. I've prepared {k} recommendations to appear in its answers as well as on Google, all implemented with your website.",
        "geo_cta": "Read my audit",
        "mockup_kicker": "Your mock-up",
        "mockup_title": "Your new website is already built",
        "mockup_text": "I designed a free first version of {name}'s website: mobile-first, fast, with one-tap contact and directions. It's live and ready to be tailored to you.",
        "mockup_cta": "View the mock-up",
        "preview_alt": "Preview of the new website for",
        "plans_title": "Our packages",
        "plans_text": "An agency-grade website, live in 24 hours to 7 days instead of several weeks. Turnkey: design, copy, launch, hosting and HTTPS included.",
        "agency": "Agency price: {range}",
        "agency_delay": "{delay}",
        "saving": "≈ {pct}% less",
        "value_line": "Business and Premium include the full SEO & GEO audit and its implementation, which agencies bill {audit} on its own.",
        "geo_credit": "If you then rebuild your website with us, the audit fee is fully deducted (order within {days} days).",
        "recommended": "Recommended for you",
        "delivery": "live in",
        "closing": "If you like it, just reply \"YES\" to this email: I'll tailor it to your business and put it live under your name. You only pay once the site is live and you've approved it.",
        "preheader_site": "Your website scores {score}/100 — a mock-up of your new site is waiting.",
        "preheader_none": "A free mock-up of your future website is waiting for you.",
        "social_text": "Online I could only find your {platform} page: no address of your own to share, nothing that ranks on Google, and customers can't see your services or an easy way to book.",
        "no_site_phrase": "I couldn't find a website for {name}.",
        "social_phrase": "I could only find {name}'s {platform} page, no website of its own.",
        "no_site_text": "Without a website, customers searching on Google may not find your hours, your services, or an easy way to contact you or book.",
    },
}

# Accroches courtes (objet d'email, relance, WhatsApp) selon le problème le plus grave
HOOKS = {
    "fr": {
        "expired": "votre site a disparu d'internet",
        "parked": "votre site a disparu d'internet",
        "listing": "votre site ne s'affiche plus",
        "down": "votre site est inaccessible",
        "empty": "votre site semble vide",
        "no_https": "votre site affiche « Non sécurisé »",
        "bad_ssl": "vos visiteurs voient une alerte de sécurité",
        "not_mobile": "votre site est illisible sur smartphone",
        "very_slow": "votre site met trop de temps à charger",
        "legacy_tech": "votre site a pris un coup de vieux",
        "stale": "votre site n'est plus à jour",
        "slow": "votre site est lent",
        "_": "quelques améliorations rapides pour votre site",
    },
    "en": {
        "expired": "your website has disappeared from the internet",
        "parked": "your website has disappeared from the internet",
        "listing": "your website no longer loads",
        "down": "your website is down",
        "empty": "your website looks empty",
        "no_https": "your website shows \"Not secure\"",
        "bad_ssl": "visitors get a security warning on your site",
        "not_mobile": "your website is hard to use on phones",
        "very_slow": "your website takes too long to load",
        "legacy_tech": "your website is looking dated",
        "stale": "your website is out of date",
        "slow": "your website is slow",
        "_": "a few quick wins for your website",
    },
}


def de_name(name: str) -> str:
    """Préposition « de » avec élision devant voyelle ou h muet : « d'Institut », « de Shine Spa », « du 44 Rue des Fripiers »."""
    name = name.strip()
    for article, contracted in (("Le ", "du "), ("Les ", "des ")):
        if name.startswith(article) or name.startswith(article.lower()):
            return contracted + name[len(article):]
    first = name[:1].lower()
    if first == "h":  # h aspiré par défaut (Happy, Hong Kong…) ; élision seulement pour les h muets connus
        mute = name.lower().split(" ")[0] in {"hôtel", "hotel", "hammam", "herboristerie", "horlogerie", "huilerie", "hôtellerie"}
        return f"d'{name}" if mute else f"de {name}"
    return f"d'{name}" if first and first in "aeiouyâàéèêîïôûœ" else f"de {name}"


def _geo_offer(cfg: dict[str, Any], lead: dict[str, Any], lang: str) -> dict[str, Any] | None:
    offers = geo_offers(cfg, lead.get("market"), lang)
    return offers[0] if offers else None


def _launch_offer(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Offre de lancement (premiers clients) : site en ligne N jours sans payer, réglé seulement s'il est gardé."""
    lo = cfg["outreach"].get("launch_offer") or {}
    return {"slots": lo.get("slots", 5), "days": lo.get("trial_days", 14)} if lo.get("enabled") else None


def _first_touch_kind(cfg: dict[str, Any], kind: str, offer: str | None) -> str:
    """Premier contact court (une question, pas de grille de prix) sauf offre GEO."""
    short = cfg["outreach"].get("first_touch", "short") == "short"
    return "initial_short" if kind == "initial" and short and offer != "geo" else kind


def _geo_line(lead: dict[str, Any], s: dict[str, Any], has_site: bool, site_down: bool, lang: str) -> str:
    """Phrase d'accroche de l'audit SEO & GEO (chiffres issus de l'audit réel)."""
    geo = (lead.get("extra") or {}).get("geo")
    if has_site and geo:
        return s["geo_site"].format(g=geo_score(geo), k=len(geo_findings(geo, lang)))
    return s["geo_none"].format(k=len(geo_findings(None, lang, has_site=False, site_down=site_down)))


def _context(lead: dict[str, Any], cfg: dict[str, Any], mockup_url: str | None) -> dict[str, Any]:
    m = market(cfg, lead.get("market"))
    lang = m.get("language", "fr")
    tiers = pricing_table(cfg, lead.get("market"), lead.get("recommended_tier"))
    recommended = next((t for t in tiers if t["recommended"]), tiers[1])
    issues = [{**i, "label": issue_label(i["code"], i.get("params", {}), lang)} for i in lead.get("issues") or []]
    codes = [i["code"] for i in issues]
    site_down = bool(lead.get("website")) and bool({"down", "expired", "parked", "listing"} & set(codes))
    has_site = bool(lead.get("website")) and not site_down and "no_site" not in codes
    hooks = HOOKS[lang]
    cat = cfg["prospecting"]["categories"].get(lead.get("category") or "", {})
    s = STRINGS[lang]
    host = urlparse(lead["website"]).netloc.removeprefix("www.") if lead.get("website") else ""
    fmt = {"name": lead["name"], "de_name": de_name(lead["name"]), "cat": cat.get(lang) or ("commerce" if lang == "fr" else "business"),
           "city": lead.get("city") or ("votre ville" if lang == "fr" else "your area"), "host": host}
    fmt["un_cat"] = f"{cat.get('fr_art', 'un')} {fmt['cat']}"
    fmt["a_cat"] = f"{'an' if fmt['cat'][:1].lower() in 'aeiou' else 'a'} {fmt['cat']}"
    platform = None if lead.get("website") else social_platform(lead.get("extra"))
    fmt["platform"] = platform or ""
    intro = s["intro_site" if has_site else "intro_down" if site_down else "intro_social" if platform else "intro_none"].format(**fmt)
    no_site_sentence = s["social_phrase" if platform else "no_site_phrase"].format(**fmt)
    quoted = (lead.get("extra") or {}).get("quoted") or {}
    offers = geo_offers(cfg, lead.get("market"), lang)
    if quoted.get("prices") or quoted.get("from"):  # prix de la grille annoncés dans le premier email
        prices = dict(quoted.get("prices") or {})
        if quoted.get("from"):
            prices.setdefault(tiers[0]["short"], quoted["from"])
        tiers = [{**t, "price": prices[t["short"]], "agency": t.get("agency") and {**t["agency"], "saving": None}}
                 if t["short"] in prices and prices[t["short"]] != t["price"] else t for t in tiers]
        recommended = next((t for t in tiers if t["short"] == recommended["short"]), recommended)
    if quoted.get("tier"):  # formule conseillée dans le premier email (la recommandation a pu évoluer)
        recommended = next((t for t in tiers if t["short"] == quoted["tier"]), recommended)
    if quoted.get("recommended") and quoted["recommended"] != recommended["price"]:
        agency = recommended.get("agency") and {**recommended["agency"], "saving": None}
        recommended = {**recommended, "price": quoted["recommended"], "agency": agency}
    for i, price in enumerate(quoted.get("geo") or []):
        if i < len(offers) and price != offers[i]["price"]:
            offers[i] = {**offers[i], "price": price,
                         "agency": offers[i].get("agency") and {**offers[i]["agency"], "saving": None}}
    return {
        "intro": intro,
        "audit_url": (mockup_url + "audit/") if mockup_url else None,
        "offer": (lead.get("extra") or {}).get("offer"),
        "geo_score": geo_score((lead.get("extra") or {}).get("geo")),
        "geo_top": geo_findings((lead.get("extra") or {}).get("geo"), lang)[:3],
        "geo_offer": offers[0] if offers else None,
        "geo_offers": offers,
        "cat": fmt["cat"], "un_cat": fmt["un_cat"], "a_cat": fmt["a_cat"], "city": fmt["city"],
        "geo_line": _geo_line(lead, s, has_site, site_down, lang),
        "platform": platform,
        "no_site_sentence": no_site_sentence,
        "de_name": de_name(lead["name"]),
        "host": host,
        "lang": lang,
        "lead": lead,
        "business": cfg["business"],
        "market": m,
        "issues": issues,
        "score": lead.get("score"),
        "has_site": has_site,
        "site_down": site_down,
        "hook": next((hooks[c] for c in codes if c in hooks), hooks["_"]),
        "tiers": tiers,
        "recommended": recommended,
        "audit_value": (agency_benchmark(m, "audit") or {}).get("range"),
        "launch": _launch_offer(cfg),
        "emailed": lead.get("status") in ("contacted", "replied"),
        "geo_credit_days": cfg["pricing"].get("geo_credit_days"),
        "s": s,
        "mockup_url": mockup_url,
        "category_label": cat.get(lang) or ("commerce" if lang == "fr" else "business"),
    }


def render_email(kind: str, lead: dict[str, Any], cfg: dict[str, Any], *,
                 mockup_url: str | None = None, original_subject: str = "") -> tuple[str, str]:
    """kind ∈ {initial, followup_1, followup_2, followup_3}. Retourne (sujet, corps)."""
    ctx = _context(lead, cfg, mockup_url)
    ctx["original_subject"] = original_subject
    lang = ctx["market"].get("language", "fr")
    suffix = "_geo" if ctx.get("offer") == "geo" else ""
    kind = _first_touch_kind(cfg, kind, ctx.get("offer"))
    raw = _env.get_template(f"emails/{lang}/{kind}{suffix}.txt").render(**ctx)
    # trim_blocks peut coller le séparateur à la ligne du sujet : on découpe sur "---\n"
    subject, _, body = raw.partition("---\n")
    return subject.strip(), body.strip() + "\n"


_BG_RE = re.compile(r'<(\w+)([^>]*?)style="([^"]*?)background:\s*(#[0-9a-fA-F]{3,6})\s*;?')


def harden_backgrounds(html: str) -> str:
    """background:#xxx → background-color + attribut bgcolor : certaines messageries ignorent le raccourci CSS."""
    def fix(m: re.Match) -> str:
        tag, attrs, before, color = m.groups()
        extra = f' bgcolor="{color}"' if tag.lower() in ("td", "table", "body", "tr") and "bgcolor=" not in attrs else ""
        return f'<{tag}{attrs}{extra} style="{before}background-color:{color};'
    return _BG_RE.sub(fix, html)


def _phone(lead: dict[str, Any], cfg: dict[str, Any], lang: str, category_label: str) -> dict[str, str]:
    """Mêmes choix que la maquette web (accroche, couleur) : l'aperçu de l'email et le site concordent."""
    from .mockup import STRINGS as MOCKUP_STRINGS, mockup_spec
    spec = mockup_spec(lead, cfg)
    t = MOCKUP_STRINGS[lang]
    return {"accent": spec["accent"], "bg": spec["bg"], "tagline": spec["tagline"], "cta": spec["cta"],
            "eyebrow": spec["eyebrow"], "intro": (spec["lede"][:90] + "…") if len(spec["lede"]) > 90 else (spec["lede"] or t["intro"]),
            "chip": " · ".join(spec["facts"][:1]) or t["chip_booking"]}


def _linkify(text: str) -> Markup:
    """Liens cliquables, affichés sans « https:// » ni barre finale (plus lisible)."""
    def link(m: re.Match) -> str:
        url = m.group(1)
        label = re.sub(r"^https?://", "", url).rstrip("/")
        return f'<a href="{url}" style="color:#1a1614; text-decoration:underline;">{label}</a>'
    return Markup(re.sub(r"(https?://[^\s<]+)", link, str(escape(text))))


def render_email_html(kind: str, lead: dict[str, Any], cfg: dict[str, Any], *, mockup_url: str | None = None,
                      preview_url: str | None = None, original_subject: str = "") -> str:
    """Version HTML (mise en page soignée) ; la version texte reste l'alternative."""
    ctx = _context(lead, cfg, mockup_url)
    lang = ctx["lang"]
    s = STRINGS[lang]
    subject, text = render_email(kind, lead, cfg, mockup_url=mockup_url, original_subject=original_subject)
    ctx.update(c=COLORS, f=FONTS, s=s, subject=subject, preview_url=preview_url if mockup_url else None,
               phone=_phone(lead, cfg, lang, ctx["category_label"]))
    if ctx.get("offer") == "geo":
        return _render_paragraphs_html("emails/html/geo.html", ctx, cfg, text, kind)
    if _first_touch_kind(cfg, kind, ctx.get("offer")) == "initial_short":
        # Premier contact : un email personnel, sans mise en page (meilleure délivrabilité, plus de réponses)
        rows = [b for b in text.split("\n\n") if b.strip()]
        ctx.update(subject=subject, paragraphs=[Markup("<br>").join(_linkify(r) for r in b.strip().split("\n")) for b in rows])
        return _html_env.get_template("emails/html/plain.html").render(**ctx)
    if kind == "initial":
        score = ctx["score"] or 0
        ctx["score"] = score
        ctx["score_color"] = COLORS["danger"] if score < 40 else COLORS["warn"]
        ctx["verdict"] = s["verdicts"][0 if score < 30 else 1 if score < 50 else 2]
        codes = [i["code"] for i in ctx["issues"]]
        ctx["headline_problem"] = next((s["problem"][c] for c in codes if c in s["problem"]), s["problem"]["no_site"])
        if ctx["platform"]:
            ctx["headline_problem"] = s["problem"]["social_only"]
        ctx["no_site_text"] = s["social_text"].format(platform=ctx["platform"]) if ctx["platform"] else s["no_site_text"]
        ctx["preheader"] = (s["preheader_site"].format(score=score) if ctx["has_site"] else s["preheader_none"])
        return harden_backgrounds(_html_env.get_template("emails/html/initial.html").render(**ctx))
    return _render_paragraphs_html("emails/html/followup.html", ctx, cfg, text, kind)


def _render_paragraphs_html(template: str, ctx: dict[str, Any], cfg: dict[str, Any], text: str, kind: str) -> str:
    """Le texte (volontairement personnel) mis en page, sans la signature texte."""
    sender = cfg["business"]["sender_name"].strip()
    lines = text.split("\n")
    if sender in (l.strip() for l in lines):
        lines = lines[:[l.strip() for l in lines].index(sender)]
    blocks = [b for b in "\n".join(lines).split("\n\n") if b.strip()]
    paragraphs = [" ".join(b.split("\n")).strip() for b in blocks]

    def fmt(block: str) -> Markup:
        rows = [r.strip() for r in block.split("\n") if r.strip()]
        if any(r.startswith(("•", "–")) or re.match(r"\d+\. ", r) for r in rows):  # listes : une ligne par élément
            return Markup("<br>").join(_linkify(r) for r in rows)
        return _linkify(" ".join(rows))
    ctx.update(paragraphs=[fmt(b) for b in blocks], show_mockup=kind != "followup_3",
               preheader=paragraphs[1] if len(paragraphs) > 1 else "")
    return harden_backgrounds(_html_env.get_template(template).render(**ctx))


def render_whatsapp(lead: dict[str, Any], cfg: dict[str, Any], mockup_url: str | None = None) -> str:
    ctx = _context(lead, cfg, mockup_url)
    lang = ctx["market"].get("language", "fr")
    return _env.get_template(f"emails/{lang}/whatsapp.txt").render(**ctx).strip()


def next_followup(lead: dict[str, Any], followup_days: list[int], now: datetime | None = None) -> str | None:
    """Retourne le type de relance dû aujourd'hui, "lost" si la séquence est finie, ou None."""
    if lead["status"] != "contacted" or not lead.get("last_contact_at"):
        return None
    now = now or datetime.now(timezone.utc)
    n = lead.get("followups_sent", 0)
    last = datetime.fromisoformat(lead["last_contact_at"])
    if n >= len(followup_days):
        # Laisse une semaine après la dernière relance avant de clore
        return "lost" if now - last >= timedelta(days=7) else None
    # Délais exprimés depuis le premier contact : on les convertit en écart depuis le contact précédent
    gap = followup_days[n] - (followup_days[n - 1] if n else 0)
    return f"followup_{n + 1}" if now - last >= timedelta(days=gap) else None


STOP_WORDS = ("stop", "désinscri", "desinscri", "unsubscribe", "ne plus être contacté", "pas intéressé",
              "not interested", "remove me", "no thanks", "non merci")


def is_optout_reply(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in STOP_WORDS)
