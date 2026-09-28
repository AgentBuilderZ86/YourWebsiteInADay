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


def agency_benchmark(m: dict[str, Any], key: str, amount: int | float | None = None) -> dict[str, Any] | None:
    """Fourchette d'une agence pour la même prestation (markets.<code>.benchmarks), et l'économie
    par rapport au milieu de la fourchette (affichée seulement si elle dépasse 15 %)."""
    b = (m.get("benchmarks") or {}).get(key)
    if not b:
        return None
    low, high = b[0], b[1]
    sep = " à " if m.get("language") == "fr" else " to "
    if m.get("currency_prefix"):
        rng = f"{format_price(low, m)}{sep}{format_price(high, m)}"
    else:
        rng = f"{format_price(low, m).rsplit(' ', 1)[0]}{sep}{format_price(high, m)}"
    saving = None
    if amount:
        pct = round((1 - amount / ((low + high) / 2)) * 100)
        saving = pct if pct >= 15 else None
    return {"range": rng, "low": low, "high": high, "delay": b[2] if len(b) > 2 else None, "saving": saving,
            "note": b[3] if len(b) > 3 else None}


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
            "agency": agency_benchmark(m, key, m["prices"][key]),
        })
    return rows


def in_send_window(cfg: dict[str, Any], market_code: str | None, now: datetime | None = None) -> bool:
    """Vrai si c'est un jour d'envoi, en heures de bureau, chez le destinataire."""
    m = market(cfg, market_code)
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(m.get("timezone", "UTC")))
    start, end = cfg["outreach"].get("send_window", [8, 18])
    days = cfg["outreach"].get("send_weekdays", [0, 1, 2, 3, 4])
    return now.weekday() in days and start <= now.hour < end


def can_email_market(cfg: dict[str, Any], market_code: str | None) -> tuple[bool, str]:
    m = market(cfg, market_code)
    if not m.get("enabled"):
        return False, "marché désactivé"
    if m.get("requires_postal_address") and not cfg["business"].get("postal_address"):
        return False, "adresse postale de l'expéditeur requise (business.postal_address)"
    return True, ""


def geo_offers(cfg: dict[str, Any], market_code: str | None, lang: str | None = None) -> list[dict[str, Any]]:
    """Offres GEO du marché (`geo_offers` : correctifs prioritaires + optimisation complète), ou à
    défaut « Audit GEO complet » (prix `geo`). L'audit de base reste offert à chaque prospect."""
    m = market(cfg, market_code)
    lang = lang or m.get("language", "fr")
    keys = m.get("geo_offers") or (["geo_audit"] if "geo" in m.get("prices", {}) else [])
    out = []
    for key in keys:
        spec = cfg["pricing"].get(key)
        amount = m["prices"].get("geo" if key == "geo_audit" else key)
        if not spec or amount is None:
            continue
        out.append({"key": key, "label": spec["label"][lang], "delivery": spec["delivery"][lang],
                    "features": spec["features"][lang], "price": format_price(amount, m), "amount": amount,
                    "agency": agency_benchmark(m, key, amount) or agency_benchmark(m, "audit", amount),
                    "recommended": key == "geo_full"})
    return out
