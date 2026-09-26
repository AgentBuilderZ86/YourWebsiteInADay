"""Rédaction des messages de prospection et gestion de la séquence de relances."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jinja2 import Environment, PackageLoader

from .pricing import pricing_table

_env = Environment(loader=PackageLoader("ywiad", "templates"), autoescape=False,
                   trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)

CATEGORY_LABELS = {
    "restaurant": "restaurant", "cafe": "café", "hairdresser": "coiffeur", "beauty": "institut de beauté",
    "bakery": "boulanger", "clothes": "magasin de vêtements", "florist": "fleuriste", "optician": "opticien",
    "dentist": "dentiste", "hotel": "hôtel", "car_repair": "garagiste",
}


# Accroches courtes (objet d'email, relance, WhatsApp) selon le problème le plus grave
HOOKS = {
    "down": "votre site est inaccessible",
    "empty": "votre site semble vide",
    "no_https": "votre site affiche « Non sécurisé »",
    "bad_ssl": "vos visiteurs voient une alerte de sécurité",
    "not_mobile": "votre site est illisible sur smartphone",
    "very_slow": "votre site met trop de temps à charger",
    "legacy_tech": "votre site a pris un coup de vieux",
    "stale": "votre site n'est plus à jour",
    "slow": "votre site est lent",
}
DEFAULT_HOOK = "quelques améliorations rapides pour votre site"


def _context(lead: dict[str, Any], cfg: dict[str, Any], mockup_url: str | None) -> dict[str, Any]:
    tiers = pricing_table(cfg, lead.get("recommended_tier"))
    recommended = next((t for t in tiers if t["recommended"]), tiers[1])
    issues = lead.get("issues") or []
    has_site = bool(lead.get("website")) and not any(i["code"] in ("no_site", "down") for i in issues)
    hook = next((HOOKS[i["code"]] for i in issues if i["code"] in HOOKS), DEFAULT_HOOK)
    return {
        "hook": hook,
        "lead": lead,
        "business": cfg["business"],
        "issues": issues,
        "score": lead.get("score"),
        "has_site": has_site,
        "site_down": bool(lead.get("website")) and any(i["code"] == "down" for i in issues),
        "tiers": tiers,
        "recommended": recommended,
        "mockup_url": mockup_url,
        "category_label": CATEGORY_LABELS.get(lead.get("category") or "", "commerce"),
    }


def render_email(kind: str, lead: dict[str, Any], cfg: dict[str, Any], *,
                 mockup_url: str | None = None, original_subject: str = "") -> tuple[str, str]:
    """kind ∈ {initial, followup_1, followup_2, followup_3}. Retourne (sujet, corps)."""
    ctx = _context(lead, cfg, mockup_url)
    ctx["original_subject"] = original_subject
    raw = _env.get_template(f"emails/{kind}.txt").render(**ctx)
    # trim_blocks peut coller le séparateur à la ligne du sujet : on découpe sur "---\n"
    subject, _, body = raw.partition("---\n")
    return subject.strip(), body.strip() + "\n"


def render_whatsapp(lead: dict[str, Any], cfg: dict[str, Any], mockup_url: str | None = None) -> str:
    return _env.get_template("emails/whatsapp.txt").render(**_context(lead, cfg, mockup_url)).strip()


def next_followup(lead: dict[str, Any], followup_days: list[int], now: datetime | None = None) -> str | None:
    """Retourne le type de relance dû aujourd'hui, "lost" si la séquence est finie, ou None."""
    if lead["status"] != "contacted" or not lead.get("last_contact_at"):
        return None
    now = now or datetime.now(timezone.utc)
    n = lead.get("followups_sent", 0)
    if n >= len(followup_days):
        # Laisse une semaine après la dernière relance avant de clore
        last = datetime.fromisoformat(lead["last_contact_at"])
        return "lost" if now - last >= timedelta(days=7) else None
    # Délais exprimés depuis le premier contact : on les convertit en écart depuis le contact précédent
    gap = followup_days[n] - (followup_days[n - 1] if n else 0)
    last = datetime.fromisoformat(lead["last_contact_at"])
    return f"followup_{n + 1}" if now - last >= timedelta(days=gap) else None


STOP_WORDS = ("stop", "désinscri", "desinscri", "unsubscribe", "ne plus être contacté", "pas intéressé")


def is_optout_reply(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in STOP_WORDS)
