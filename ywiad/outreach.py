"""Rédaction des messages de prospection (FR / EN) et gestion de la séquence de relances."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jinja2 import Environment, PackageLoader

from .audit import issue_label
from .pricing import market, pricing_table

_env = Environment(loader=PackageLoader("ywiad", "templates"), autoescape=False,
                   trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)

# Accroches courtes (objet d'email, relance, WhatsApp) selon le problème le plus grave
HOOKS = {
    "fr": {
        "expired": "votre site a disparu d'internet",
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


def _context(lead: dict[str, Any], cfg: dict[str, Any], mockup_url: str | None) -> dict[str, Any]:
    m = market(cfg, lead.get("market"))
    lang = m.get("language", "fr")
    tiers = pricing_table(cfg, lead.get("market"), lead.get("recommended_tier"))
    recommended = next((t for t in tiers if t["recommended"]), tiers[1])
    issues = [{**i, "label": issue_label(i["code"], i.get("params", {}), lang)} for i in lead.get("issues") or []]
    codes = [i["code"] for i in issues]
    site_down = bool(lead.get("website")) and ("down" in codes or "expired" in codes)
    has_site = bool(lead.get("website")) and not site_down and "no_site" not in codes
    hooks = HOOKS[lang]
    cat = cfg["prospecting"]["categories"].get(lead.get("category") or "", {})
    return {
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
