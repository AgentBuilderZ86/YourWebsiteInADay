"""Maquette de refonte instantanée (one-page) par prospect, et page d'accueil de l'offre."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from .pricing import enabled_markets, market, pricing_table

log = logging.getLogger("ywiad")

_env = Environment(
    loader=PackageLoader("ywiad", "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)

# Palette, accroche et bouton d'action par métier
THEMES: dict[str, dict[str, Any]] = {
    "restaurant": {"accent": "#b4442c", "bg": "#fbf6f1", "icon": "🍽️",
                   "fr": ("Une cuisine qui se partage", "Réserver une table"), "en": ("Food worth sharing", "Book a table")},
    "cafe": {"accent": "#6b4226", "bg": "#f8f3ee", "icon": "☕",
             "fr": ("Votre pause, notre savoir-faire", "Nous trouver"), "en": ("Your break, our craft", "Find us")},
    "hairdresser": {"accent": "#7a3e9d", "bg": "#f7f3fa", "icon": "✂️",
                    "fr": ("Votre style, notre passion", "Prendre rendez-vous"), "en": ("Your style, our passion", "Book an appointment")},
    "beauty": {"accent": "#b0567a", "bg": "#fbf4f7", "icon": "🌸",
               "fr": ("Prenez soin de vous", "Prendre rendez-vous"), "en": ("Take time for yourself", "Book an appointment")},
    "bakery": {"accent": "#b7791f", "bg": "#fdf8ef", "icon": "🥐",
               "fr": ("Fait maison, chaque matin", "Commander"), "en": ("Baked fresh every morning", "Order now")},
    "clothes": {"accent": "#1f2937", "bg": "#f5f5f4", "icon": "👗",
                "fr": ("La nouvelle collection est arrivée", "Voir la boutique"), "en": ("The new collection is here", "Shop now")},
    "florist": {"accent": "#2f855a", "bg": "#f2f8f4", "icon": "💐",
                "fr": ("Des fleurs pour chaque moment", "Commander un bouquet"), "en": ("Flowers for every moment", "Order a bouquet")},
    "optician": {"accent": "#1e5fa8", "bg": "#f1f6fc", "icon": "👓",
                 "fr": ("Voir mieux, être bien", "Prendre rendez-vous"), "en": ("See better, feel better", "Book an eye test")},
    "dentist": {"accent": "#0f766e", "bg": "#f0f9f8", "icon": "🦷",
                "fr": ("Des soins en toute confiance", "Prendre rendez-vous"), "en": ("Dental care you can trust", "Book an appointment")},
    "hotel": {"accent": "#8a6d3b", "bg": "#faf7f1", "icon": "🏨",
              "fr": ("Votre séjour, pensé dans les moindres détails", "Réserver"), "en": ("Your stay, down to the last detail", "Book your stay")},
    "car_repair": {"accent": "#c2410c", "bg": "#f7f5f3", "icon": "🔧",
                   "fr": ("Entretien et réparation, en toute transparence", "Demander un devis"), "en": ("Honest servicing and repairs", "Get a quote")},
    "estate_agent": {"accent": "#334155", "bg": "#f4f6f8", "icon": "🏡",
                     "fr": ("Trouvez le bien qui vous ressemble", "Estimer mon bien"), "en": ("Find the home that fits you", "Get a valuation")},
}
DEFAULT_THEME = {"accent": "#2563eb", "bg": "#f6f8fb", "icon": "⭐",
                 "fr": ("Bienvenue chez nous", "Nous contacter"), "en": ("Welcome", "Contact us")}

STRINGS = {
    "fr": {
        "banner": "Maquette de démonstration réalisée par {biz} pour {name}", "more": "en savoir plus",
        "call": "Appeler", "directions": "Itinéraire", "intro": "Retrouvez nos services, nos horaires et contactez-nous en un clic.",
        "in": "à", "services": "Nos services", "services_txt": "Présentez ici vos prestations phares, vos prix et vos nouveautés.",
        "hours": "Horaires", "hours_txt": "Horaires à compléter", "find": "Nous trouver", "find_txt": "Adresse à compléter",
        "reviews": "Avis clients", "reviews_txt": "Vos meilleurs avis Google mis en avant pour rassurer les nouveaux clients.",
        "made": "Site réalisé en 24 h par",
        "why": "Tout ce que vos clients cherchent, au même endroit", "chip_booking": "Réservation en ligne",
        "chip_local": "Près de chez vous",
    },
    "en": {
        "banner": "Demo mock-up made by {biz} for {name}", "more": "learn more",
        "call": "Call", "directions": "Directions", "intro": "Discover our services and opening hours, and reach us in one tap.",
        "in": "in", "services": "Our services", "services_txt": "Showcase your key services, prices and latest news here.",
        "hours": "Opening hours", "hours_txt": "Opening hours to be added", "find": "Find us", "find_txt": "Address to be added",
        "reviews": "Reviews", "reviews_txt": "Your best Google reviews, front and centre to reassure new customers.",
        "made": "Website built in 24 hours by",
        "why": "Everything your customers look for, in one place", "chip_booking": "Online booking",
        "chip_local": "Near you",
    },
}

DIAL_CODES = {"MA": "212", "FR": "33", "BE": "32", "CA": "1", "US": "1", "AU": "61", "AE": "971", "GB": "44"}


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "site"


def whatsapp_number(phone: str | None, country_code: str | None = "MA") -> str | None:
    """Numéro au format international sans « + » (pour wa.me)."""
    if not phone:
        return None
    raw = phone.split(";")[0].strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+"):
        return digits or None
    if digits.startswith("00"):
        return digits[2:]
    dial = DIAL_CODES.get(country_code or "", "")
    if digits.startswith("0"):
        digits = digits[1:]
    return (dial + digits) if digits else None


def mockup_slug(lead: dict[str, Any]) -> str:
    return f"{slugify(lead['name'])}-{lead['id']}"


def render_mockup(lead: dict[str, Any], cfg: dict[str, Any]) -> str:
    m = market(cfg, lead.get("market"))
    lang = m.get("language", "fr")
    theme = THEMES.get(lead.get("category") or "", DEFAULT_THEME)
    tagline, cta = theme[lang]
    return _env.get_template("mockups/onepage.html").render(
        lead=lead, theme=theme, tagline=tagline, cta=cta, lang=lang, t=STRINGS[lang],
        category_label=cfg["prospecting"]["categories"].get(lead.get("category") or "", {}).get(lang, ""),
        hours=lead.get("extra", {}).get("opening_hours"),
        whatsapp=whatsapp_number(lead.get("phone"), m.get("country_code")),
        business=cfg["business"],
    )


def write_mockup(lead: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, str | None]:
    """Écrit la maquette sur disque. Retourne (chemin local, URL publique éventuelle)."""
    slug = mockup_slug(lead)
    out = Path(cfg["paths"]["mockups"]) / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_mockup(lead, cfg), encoding="utf-8")
    base = (cfg["business"].get("mockup_base_url") or "").rstrip("/")
    return str(out / "index.html"), (f"{base}/{slug}/" if base else None)


def write_landing(cfg: dict[str, Any]) -> str:
    """Page d'accueil de l'offre, avec la grille tarifaire de chaque marché."""
    markets = [
        {"code": code, "name": market(cfg, code)["name"], "lang": market(cfg, code).get("language", "fr"),
         "tiers": pricing_table(cfg, code)}
        for code in enabled_markets(cfg)
    ]
    out = Path(cfg["paths"]["site"])
    out.mkdir(parents=True, exist_ok=True)
    html = _env.get_template("site/index.html").render(business=cfg["business"], markets=markets)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "robots.txt").write_text("User-agent: *\nDisallow: /demo/\n", encoding="utf-8")
    # Le dossier est déployé tel quel (on ne déploie jamais la racine du dépôt, qui contient la base)
    (out / "netlify.toml").write_text(
        '[build]\n  publish = "."\n  command = ""\n\n[[headers]]\n  for = "/demo/*"\n'
        '  [headers.values]\n    X-Robots-Tag = "noindex"\n', encoding="utf-8")
    return str(out / "index.html")


def render_previews(html_paths: list[str]) -> int:
    """Capture (preview.png, format mobile) de chaque maquette qui n'en a pas encore.

    Utilise Playwright côté Node (préinstallé dans l'environnement cloud). Sans Node/Playwright,
    les emails partent simplement sans image.
    """
    jobs = [[str(Path(h).resolve()), str(Path(h).resolve().with_name("preview.png"))]
            for h in html_paths if not Path(h).with_name("preview.png").exists()]
    if not jobs or not shutil.which("node"):
        return 0
    env = dict(os.environ)
    try:
        root = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=30).stdout.strip()
        env["NODE_PATH"] = root + (os.pathsep + env["NODE_PATH"] if env.get("NODE_PATH") else "")
        out = subprocess.run(["node", str(Path(__file__).with_name("screenshot.js")), json.dumps(jobs)],
                             capture_output=True, text=True, timeout=60 + 10 * len(jobs), env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("captures impossibles : %s", exc)
        return 0
    if out.returncode:
        log.warning("captures impossibles : %s", out.stderr.strip()[:300])
    return out.stdout.count("ok ")


def preview_path(lead: dict[str, Any], cfg: dict[str, Any]) -> Path:
    return Path(cfg["paths"]["mockups"]) / mockup_slug(lead) / "preview.png"
