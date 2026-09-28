"""Rédaction des messages de prospection (FR / EN) et gestion de la séquence de relances."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import re
from urllib.parse import urlparse

from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup, escape

from .audit import issue_label
from .pricing import market, pricing_table

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
        "intro_site": "J'ai découvert le site {de_name} en cherchant un {cat} à {city}. Je conçois des sites pour les commerces indépendants, alors j'ai pris quelques minutes pour l'analyser : voici ce qu'un client voit en arrivant — et ce qui le fait souvent repartir.",
        "intro_down": "En cherchant un {cat} à {city}, j'ai voulu consulter le site {de_name} ({host})… mais il ne s'affiche plus. Chaque client qui tombe sur une erreur part chez un concurrent, et Google finit par retirer le site de ses résultats.",
        "intro_none": "En cherchant un {cat} à {city}, j'ai trouvé {name}, mais aucun site web. Aujourd'hui, la plupart des clients vérifient horaires, adresse et avis en ligne avant de se déplacer — sans site, ils choisissent souvent un concurrent.",
        "audit_label": "Audit express",
        "verdicts": ("Critique", "À refaire", "À moderniser"),
        "problem": {"no_site": "Aucun site web trouvé", "expired": "Votre site a disparu d'internet",
                    "parked": "Votre site a disparu d'internet", "down": "Votre site ne s'affiche plus"},
        "mockup_kicker": "Votre maquette",
        "mockup_title": "Votre nouveau site est déjà prêt",
        "mockup_text": "J'ai conçu gratuitement une première version du site {de_name} : pensée pour le mobile, rapide, avec prise de contact et itinéraire en un geste. Elle est en ligne, prête à être personnalisée.",
        "mockup_cta": "Voir la maquette",
        "preview_alt": "Aperçu du futur site de",
        "plans_title": "Nos formules",
        "plans_text": "Clé en main : design, textes, mise en ligne, hébergement et HTTPS compris.",
        "recommended": "Conseillé pour vous",
        "delivery": "livré en",
        "closing": "Si le rendu vous plaît, je vous propose un échange de 10 minutes cette semaine pour l'adapter à votre activité. Il suffit de répondre à cet email.",
        "preheader_site": "Votre site obtient {score}/100 — une maquette de votre nouveau site vous attend.",
        "preheader_none": "Une maquette de votre futur site vous attend, gratuitement.",
        "no_site_text": "Sur Google, vos clients ne trouvent ni vos horaires, ni vos services, ni un moyen simple de vous contacter ou de réserver.",
    },
    "en": {
        "hello": "Hi,",
        "tagline": "Web studio · websites live in 24 hours",
        "intro_site": "I came across {name}'s website while looking for a {cat} in {city}. I design websites for independent businesses, so I took a few minutes to review it: here's what a customer sees when they land — and what often makes them leave.",
        "intro_down": "While looking for a {cat} in {city}, I tried to visit {name}'s website ({host})… but it no longer loads. Every customer who hits an error goes to a competitor, and Google eventually drops the site from its results.",
        "intro_none": "While looking for a {cat} in {city}, I found {name} but no website. Most customers now check opening hours, location and reviews online before visiting — without a site, many pick a competitor.",
        "audit_label": "Quick audit",
        "verdicts": ("Critical", "Needs a rebuild", "Needs updating"),
        "problem": {"no_site": "No website found", "expired": "Your website has vanished",
                    "parked": "Your website has vanished", "down": "Your website no longer loads"},
        "mockup_kicker": "Your mock-up",
        "mockup_title": "Your new website is already built",
        "mockup_text": "I designed a free first version of {name}'s website: mobile-first, fast, with one-tap contact and directions. It's live and ready to be tailored to you.",
        "mockup_cta": "View the mock-up",
        "preview_alt": "Preview of the new website for",
        "plans_title": "Our packages",
        "plans_text": "Turnkey: design, copy, launch, hosting and HTTPS included.",
        "recommended": "Recommended for you",
        "delivery": "live in",
        "closing": "If you like it, let's have a 10-minute chat this week to tailor it to your business. Just reply to this email.",
        "preheader_site": "Your website scores {score}/100 — a mock-up of your new site is waiting.",
        "preheader_none": "A free mock-up of your future website is waiting for you.",
        "no_site_text": "On Google, customers can't find your hours, your services, or an easy way to contact you or book.",
    },
}

# Accroches courtes (objet d'email, relance, WhatsApp) selon le problème le plus grave
HOOKS = {
    "fr": {
        "expired": "votre site a disparu d'internet",
        "parked": "votre site a disparu d'internet",
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
    return f"d'{name}" if first and first in "aeiouyhâàéèêîïôûœ" else f"de {name}"


def _context(lead: dict[str, Any], cfg: dict[str, Any], mockup_url: str | None) -> dict[str, Any]:
    m = market(cfg, lead.get("market"))
    lang = m.get("language", "fr")
    tiers = pricing_table(cfg, lead.get("market"), lead.get("recommended_tier"))
    recommended = next((t for t in tiers if t["recommended"]), tiers[1])
    issues = [{**i, "label": issue_label(i["code"], i.get("params", {}), lang)} for i in lead.get("issues") or []]
    codes = [i["code"] for i in issues]
    site_down = bool(lead.get("website")) and bool({"down", "expired", "parked"} & set(codes))
    has_site = bool(lead.get("website")) and not site_down and "no_site" not in codes
    hooks = HOOKS[lang]
    cat = cfg["prospecting"]["categories"].get(lead.get("category") or "", {})
    s = STRINGS[lang]
    host = urlparse(lead["website"]).netloc.removeprefix("www.") if lead.get("website") else ""
    fmt = {"name": lead["name"], "de_name": de_name(lead["name"]), "cat": cat.get(lang) or ("commerce" if lang == "fr" else "business"),
           "city": lead.get("city") or ("votre ville" if lang == "fr" else "your area"), "host": host}
    intro = s["intro_site" if has_site else "intro_down" if site_down else "intro_none"].format(**fmt)
    return {
        "intro": intro,
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
        "mockup_url": mockup_url,
        "category_label": cat.get(lang) or ("commerce" if lang == "fr" else "business"),
    }


def render_email(kind: str, lead: dict[str, Any], cfg: dict[str, Any], *,
                 mockup_url: str | None = None, original_subject: str = "") -> tuple[str, str]:
    """kind ∈ {initial, followup_1, followup_2, followup_3}. Retourne (sujet, corps)."""
    ctx = _context(lead, cfg, mockup_url)
    ctx["original_subject"] = original_subject
    lang = ctx["market"].get("language", "fr")
    raw = _env.get_template(f"emails/{lang}/{kind}.txt").render(**ctx)
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
    if kind == "initial":
        score = ctx["score"] or 0
        ctx["score"] = score
        ctx["score_color"] = COLORS["danger"] if score < 40 else COLORS["warn"]
        ctx["verdict"] = s["verdicts"][0 if score < 30 else 1 if score < 50 else 2]
        codes = [i["code"] for i in ctx["issues"]]
        ctx["headline_problem"] = next((s["problem"][c] for c in codes if c in s["problem"]), s["problem"]["no_site"])
        ctx["preheader"] = (s["preheader_site"].format(score=score) if ctx["has_site"] else s["preheader_none"])
        return harden_backgrounds(_html_env.get_template("emails/html/initial.html").render(**ctx))
    # Relances : le texte (volontairement personnel) mis en page, sans la signature texte
    sender = cfg["business"]["sender_name"].strip()
    lines = text.split("\n")
    if sender in (l.strip() for l in lines):
        lines = lines[:[l.strip() for l in lines].index(sender)]
    paragraphs = [" ".join(p.split("\n")).strip() for p in "\n".join(lines).split("\n\n") if p.strip()]
    ctx.update(paragraphs=[_linkify(p) for p in paragraphs], show_mockup=kind != "followup_3",
               preheader=paragraphs[1] if len(paragraphs) > 1 else "")
    return harden_backgrounds(_html_env.get_template("emails/html/followup.html").render(**ctx))


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
