"""Marchés (pays), grille tarifaire localisée et recommandation d'offre."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

TIER_ORDER = ("basique", "standard", "premium")


def market(cfg: dict[str, Any], code: str | None) -> dict[str, Any]:
    markets = cfg["markets"]
    m = markets.get(code or "") or markets.get("MA") or next(iter(markets.values()))
    return {"code": code, "country_code": m.get("country_code", code), **m}


def enabled_markets(cfg: dict[str, Any]) -> list[str]:
    return [code for code, m in cfg["markets"].items() if m.get("enabled")]


def format_price(amount: int | float, m: dict[str, Any]) -> str:
    n = f"{int(amount):,}".replace(",", " " if m.get("language") == "fr" else ",")
    cur = m.get("currency", "")
    return f"{cur}{n}" if m.get("currency_prefix") else f"{n} {cur}"


def recommend_tier(cfg: dict[str, Any], category: str | None, score: int, has_site: bool) -> str:
    """Offre de base selon le métier, ajustée selon l'état du site.

    - Site inexistant ou inaccessible : on ne monte pas au-dessus de "standard".
    - Site existant très mauvais (< 30) sur un métier "basique" : on propose "standard".
    """
    cat = cfg["prospecting"]["categories"].get(category or "", {})
    tier = cat.get("tier", "standard")
    idx = TIER_ORDER.index(tier) if tier in TIER_ORDER else 1
    if not has_site:
        idx = min(idx, 1)
    elif score < 30 and idx == 0:
        idx = 1
    return TIER_ORDER[idx]


def pricing_table(cfg: dict[str, Any], market_code: str | None, recommended: str | None = None) -> list[dict[str, Any]]:
    m = market(cfg, market_code)
    lang = m.get("language", "fr")
    per_month = " / mois" if lang == "fr" else " / month"
    rows = []
    for key in TIER_ORDER:
        t = cfg["pricing"]["tiers"][key]
        rows.append({
            "key": key,
            "label": t["label"][lang],
            "short": t["label"][lang].split(" — ")[0],
            "price": format_price(m["prices"][key], m),
            "amount": m["prices"][key],
            "monthly": format_price(m["monthly"][key], m) + per_month,
            "delivery": t["delivery"][lang],
            "features": t["features"][lang],
            "recommended": key == recommended,
        })
    return rows


def in_send_window(cfg: dict[str, Any], market_code: str | None, now: datetime | None = None) -> bool:
    """Vrai si c'est un jour ouvré, en heures de bureau, chez le destinataire."""
    m = market(cfg, market_code)
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(m.get("timezone", "UTC")))
    start, end = cfg["outreach"].get("send_window", [8, 18])
    return now.weekday() < 5 and start <= now.hour < end


def can_email_market(cfg: dict[str, Any], market_code: str | None) -> tuple[bool, str]:
    m = market(cfg, market_code)
    if not m.get("enabled"):
        return False, "marché désactivé"
    if m.get("requires_postal_address") and not cfg["business"].get("postal_address"):
        return False, "adresse postale de l'expéditeur requise (business.postal_address)"
    return True, ""
