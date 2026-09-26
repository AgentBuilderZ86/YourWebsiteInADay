"""Génération d'une maquette de refonte instantanée (one-page) pour chaque prospect."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

_env = Environment(
    loader=PackageLoader("ywiad", "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

# Palette et accroche par métier
THEMES: dict[str, dict[str, str]] = {
    "restaurant": {"accent": "#b4442c", "bg": "#fbf6f1", "tagline": "Une cuisine qui se partage", "cta": "Réserver une table", "icon": "🍽️"},
    "cafe": {"accent": "#6b4226", "bg": "#f8f3ee", "tagline": "Votre pause, notre savoir-faire", "cta": "Nous trouver", "icon": "☕"},
    "hairdresser": {"accent": "#7a3e9d", "bg": "#f7f3fa", "tagline": "Votre style, notre passion", "cta": "Prendre rendez-vous", "icon": "✂️"},
    "beauty": {"accent": "#b0567a", "bg": "#fbf4f7", "tagline": "Prenez soin de vous", "cta": "Prendre rendez-vous", "icon": "🌸"},
    "bakery": {"accent": "#b7791f", "bg": "#fdf8ef", "tagline": "Fait maison, chaque matin", "cta": "Commander", "icon": "🥐"},
    "clothes": {"accent": "#1f2937", "bg": "#f5f5f4", "tagline": "La nouvelle collection est arrivée", "cta": "Voir la boutique", "icon": "👗"},
    "florist": {"accent": "#2f855a", "bg": "#f2f8f4", "tagline": "Des fleurs pour chaque moment", "cta": "Commander un bouquet", "icon": "💐"},
    "optician": {"accent": "#1e5fa8", "bg": "#f1f6fc", "tagline": "Voir mieux, être bien", "cta": "Prendre rendez-vous", "icon": "👓"},
    "dentist": {"accent": "#0f766e", "bg": "#f0f9f8", "tagline": "Des soins en toute confiance", "cta": "Prendre rendez-vous", "icon": "🦷"},
    "hotel": {"accent": "#8a6d3b", "bg": "#faf7f1", "tagline": "Votre séjour, pensé dans les moindres détails", "cta": "Réserver", "icon": "🏨"},
    "car_repair": {"accent": "#c2410c", "bg": "#f7f5f3", "tagline": "Entretien et réparation, en toute transparence", "cta": "Demander un devis", "icon": "🔧"},
}
DEFAULT_THEME = {"accent": "#2563eb", "bg": "#f6f8fb", "tagline": "Bienvenue chez nous", "cta": "Nous contacter", "icon": "⭐"}


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "site"


def whatsapp_number(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone.split(";")[0])
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 10:  # numéro national marocain / français
        digits = "212" + digits[1:]
    return digits or None


def render_mockup(lead: dict[str, Any], cfg: dict[str, Any]) -> str:
    theme = THEMES.get(lead.get("category") or "", DEFAULT_THEME)
    return _env.get_template("mockups/onepage.html").render(
        lead=lead,
        theme=theme,
        hours=lead.get("extra", {}).get("opening_hours"),
        whatsapp=whatsapp_number(lead.get("phone")),
        business=cfg["business"],
    )


def write_mockup(lead: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, str | None]:
    """Écrit la maquette sur disque. Retourne (chemin local, URL publique éventuelle)."""
    slug = f"{slugify(lead['name'])}-{lead['id']}"
    out = Path(cfg["paths"]["mockups"]) / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_mockup(lead, cfg), encoding="utf-8")
    base = (cfg["business"].get("mockup_base_url") or "").rstrip("/")
    return str(out / "index.html"), (f"{base}/{slug}/" if base else None)
