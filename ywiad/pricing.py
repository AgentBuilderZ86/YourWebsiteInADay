"""Grille tarifaire et recommandation d'offre."""

from __future__ import annotations

from typing import Any

TIER_ORDER = ("basique", "standard", "premium")


def format_price(amount: int | float, currency: str) -> str:
    return f"{int(amount):,}".replace(",", " ") + f" {currency}"


def recommend_tier(cfg: dict[str, Any], category: str | None, score: int, has_site: bool) -> str:
    """Offre de base selon le métier, ajustée selon l'état du site.

    - Métier -> offre par défaut (ex. hôtel = premium, café = basique).
    - Site inexistant ou inaccessible : on ne monte pas au-dessus de "standard"
      (le commerçant n'a pas encore l'habitude d'investir dans le web).
    - Site existant très mauvais (< 30) sur un métier "basique" : on propose "standard",
      car une simple page ne compensera pas l'image dégradée déjà indexée.
    """
    tier = cfg["pricing"].get("category_tier", {}).get(category or "", "standard")
    idx = TIER_ORDER.index(tier) if tier in TIER_ORDER else 1
    if not has_site:
        idx = min(idx, 1)
    elif score < 30 and idx == 0:
        idx = 1
    return TIER_ORDER[idx]


def pricing_table(cfg: dict[str, Any], recommended: str | None = None) -> list[dict[str, Any]]:
    currency = cfg["business"].get("currency", "MAD")
    rows = []
    for key in TIER_ORDER:
        t = cfg["pricing"]["tiers"][key]
        rows.append({
            "key": key,
            "label": t["label"],
            "price": format_price(t["price"], currency),
            "monthly": format_price(t["monthly"], currency) + " / mois",
            "delivery": t["delivery"],
            "features": t["features"],
            "recommended": key == recommended,
        })
    return rows
