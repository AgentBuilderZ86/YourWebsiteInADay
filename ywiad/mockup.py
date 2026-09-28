"""Maquette de refonte instantanée (one-page) par prospect, et page d'accueil de l'offre."""

from __future__ import annotations

import hashlib
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

from .pricing import format_price, enabled_markets, market, pricing_table

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
        "banner_redesign": "Proposition de refonte réalisée par {biz} pour {name}, à partir des contenus de votre site actuel",
        "gallery": "En images", "contact": "Nous rendre visite", "call_us": "Nous appeler", "write": "Nous écrire",
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
        "banner_redesign": "Redesign proposal made by {biz} for {name}, built from your current website's content",
        "gallery": "Gallery", "contact": "Visit us", "call_us": "Call us", "write": "Email us",
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


# Accroches par métier (plusieurs variantes, avec la ville quand on la connaît)
TAGLINES: dict[str, dict[str, list[str]]] = {
    "fr": {
        "hotel": ["Votre séjour à {city}, pensé dans les moindres détails", "Une adresse de charme au cœur de {city}",
                  "Le calme, la douceur et l'hospitalité, à {city}"],
        "restaurant": ["La table qu'on recommande à {city}", "Une cuisine qui se partage, à {city}",
                       "Des produits frais, une cuisine sincère"],
        "cafe": ["Votre pause gourmande à {city}", "Café, douceurs et bonne humeur"],
        "hairdresser": ["Votre style, notre passion, à {city}", "Coupe, couleur et conseils sur-mesure"],
        "beauty": ["Prenez soin de vous, à {city}", "Soins, bien-être et beauté au naturel"],
        "bakery": ["Fait maison, chaque matin, à {city}", "Le bon pain et les douceurs du quartier"],
        "clothes": ["La nouvelle collection est arrivée", "Votre style, sélectionné avec soin à {city}"],
        "florist": ["Des fleurs pour chaque moment", "Bouquets et compositions sur-mesure à {city}"],
        "optician": ["Voir mieux, être bien, à {city}", "Lunettes, lentilles et conseils d'experts"],
        "dentist": ["Des soins en toute confiance, à {city}", "Votre sourire entre de bonnes mains"],
        "car_repair": ["Entretien et réparation, en toute transparence", "Votre garage de confiance à {city}"],
        "estate_agent": ["Trouvez le bien qui vous ressemble à {city}", "Acheter, vendre, louer : on s'occupe de tout"],
    },
    "en": {
        "hotel": ["Your stay in {city}, down to the last detail", "A charming address in the heart of {city}",
                  "Calm, comfort and warm hospitality in {city}"],
        "restaurant": ["The table locals recommend in {city}", "Food worth sharing, in {city}", "Fresh produce, honest cooking"],
        "cafe": ["Your favourite coffee stop in {city}", "Coffee, treats and good vibes"],
        "hairdresser": ["Your style, our passion, in {city}", "Cuts, colour and tailored advice"],
        "beauty": ["Take time for yourself in {city}", "Treatments, wellbeing and natural beauty"],
        "bakery": ["Baked fresh every morning in {city}", "Your neighbourhood bakery"],
        "clothes": ["The new collection is here", "Curated style in {city}"],
        "florist": ["Flowers for every moment", "Bespoke bouquets in {city}"],
        "optician": ["See better, feel better, in {city}", "Glasses, lenses and expert advice"],
        "dentist": ["Dental care you can trust in {city}", "Your smile in good hands"],
        "car_repair": ["Honest servicing and repairs", "Your trusted garage in {city}"],
        "estate_agent": ["Find the home that fits you in {city}", "Buy, sell, rent: we handle it all"],
    },
}
# Palettes alternatives : deux commerces du même métier n'ont pas la même maquette
ALT_ACCENTS = ["#1f4e5f", "#7a2e2e", "#2f5d50", "#4a3f8f", "#8a5a2b", "#23395d"]
LAYOUTS = ("split", "center", "editorial")


def _hash(value: str) -> int:
    return int(hashlib.md5(value.encode()).hexdigest(), 16)


def _usable_accent(color: str) -> bool:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color or ""):
        return False
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return 0.08 < lum < 0.6  # ni quasi noir, ni trop clair pour du texte blanc


CUISINES_FR = {
    "italian": "italienne", "french": "française", "pasta": "pâtes", "pizza": "pizza", "chinese": "chinoise",
    "japanese": "japonaise", "sushi": "sushi", "thai": "thaï", "vietnamese": "vietnamienne", "indian": "indienne",
    "lebanese": "libanaise", "moroccan": "marocaine", "mediterranean": "méditerranéenne", "greek": "grecque",
    "turkish": "turque", "spanish": "espagnole", "portuguese": "portugaise", "mexican": "mexicaine",
    "american": "américaine", "burger": "burgers", "crepe": "crêperie", "kebab": "kebab", "seafood": "fruits de mer",
    "fish": "poisson", "steak_house": "grillades", "regional": "régionale", "asian": "asiatique", "african": "africaine",
    "belgian": "belge", "sandwich": "sandwichs", "coffee_shop": "café", "tea": "salon de thé", "cake": "pâtisserie",
    "ice_cream": "glacier", "vegetarian": "végétarienne", "vegan": "vegan", "international": "internationale",
    "korean": "coréenne", "brasserie": "brasserie", "tapas": "tapas", "halal": "halal",
}


def mockup_spec(lead: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """Tout ce qui rend une maquette propre à CE commerce (partagé par la maquette web et l'email)."""
    m = market(cfg, lead.get("market"))
    lang = m.get("language", "fr")
    cat = lead.get("category") or ""
    theme = THEMES.get(cat, DEFAULT_THEME)
    h = _hash(lead["name"])
    extra = lead.get("extra") or {}
    site = extra.get("site") or {}
    city = (lead.get("city") or "").strip()

    options = TAGLINES[lang].get(cat) or [theme[lang][0]]
    if not city:
        options = [o for o in options if "{city}" not in o] or [theme[lang][0]]
    # Choix indépendants (tranches différentes de l'empreinte) : accroche, couleur et mise en page varient séparément
    tagline = site.get("headline") or options[(h >> 8) % len(options)].format(city=city)

    accent = site.get("theme_color") if _usable_accent(site.get("theme_color", "")) else None
    accent = accent or ([theme["accent"]] + ALT_ACCENTS)[(h >> 24) % (len(ALT_ACCENTS) + 1)]

    facts = []
    stars = str(extra.get("stars") or "").split(".")[0]
    if stars.isdigit() and 0 < int(stars) <= 5:
        facts.append("★" * int(stars))
    if extra.get("cuisine"):
        names = [c.strip().lower() for c in str(extra["cuisine"]).split(";") if c.strip()]
        labels = [CUISINES_FR.get(c, c.replace("_", " ")) if lang == "fr" else c.replace("_", " ") for c in names]
        facts.append(" · ".join(l[:1].upper() + l[1:] for l in labels))
    if city:
        facts.append(city)

    cat_label = cfg["prospecting"]["categories"].get(cat, {}).get(lang, "")
    return {
        "lang": lang,
        "accent": accent,
        "bg": theme["bg"],
        "icon": theme["icon"],
        "cta": theme[lang][1],
        "tagline": tagline,
        "lede": site.get("description") or "",
        "sections": site.get("sections") or [],
        "photos": site.get("photos") or [],
        "logo": site.get("logo") or "",
        "facts": facts,
        "eyebrow": " · ".join(p for p in (cat_label, city) if p),
        "layout": LAYOUTS[(h >> 40) % len(LAYOUTS)],
        "redesign": bool(site),
    }


def download_assets(spec: dict[str, Any], out: Path, limit: int = 6) -> tuple[list[str], str]:
    """Rapatrie les photos et le logo du site actuel (hébergés avec la maquette). Retourne (photos, logo) locaux."""
    from .audit import HEADERS
    import requests

    img_dir = out / "img"
    img_dir.mkdir(exist_ok=True)

    def fetch(url: str, name: str, min_bytes: int) -> str | None:
        for existing in img_dir.glob(name + ".*"):
            return f"img/{existing.name}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
        except requests.RequestException:
            return None
        ctype = r.headers.get("content-type", "")
        ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}.get(ctype.split(";")[0].strip())
        if r.status_code != 200 or not ext or not (min_bytes <= len(r.content) <= 4_000_000):
            return None
        (img_dir / f"{name}.{ext}").write_bytes(r.content)
        return f"img/{name}.{ext}"

    photos = [p for i, url in enumerate(spec["photos"]) if (p := fetch(url, str(i), 12_000))][:limit]
    logo = fetch(spec["logo"], "logo", 500) if spec["logo"] else None
    return photos, logo or ""


SCHEMA_TYPES = {"restaurant": "Restaurant", "cafe": "CafeOrCoffeeShop", "hotel": "Hotel", "beauty": "BeautySalon",
                "hairdresser": "HairSalon", "dentist": "Dentist", "optician": "Optician", "florist": "Florist",
                "bakery": "Bakery", "estate_agent": "RealEstateAgent", "car_repair": "AutoRepair", "bar": "BarOrPub",
                "pharmacy": "Pharmacy", "jewelry": "JewelryStore", "furniture": "FurnitureStore",
                "travel_agency": "TravelAgency", "wine_shop": "LiquorStore", "fast_food": "FastFoodRestaurant"}


def geo_markup(lead: dict[str, Any], cfg: dict[str, Any], lang: str, hours: str | None) -> tuple[list[dict[str, str]], str]:
    """FAQ et données structurées schema.org de la maquette (GEO) — uniquement à partir de faits connus."""
    name, city = lead["name"], lead.get("city") or ""
    where = lead.get("address") or city
    phone = (lead.get("phone") or "").split(";")[0].strip()
    fr = lang == "fr"
    faq = []
    if where:
        faq.append({"q": f"Où se trouve {name} ?" if fr else f"Where is {name}?", "a": where})
    if phone:
        faq.append({"q": "Comment nous contacter ou réserver ?" if fr else "How can I contact or book?",
                    "a": (f"Par téléphone au {phone}, ou directement depuis ce site." if fr
                          else f"Call {phone}, or directly from this website.")})
    if hours:
        faq.append({"q": "Quels sont les horaires ?" if fr else "What are the opening hours?", "a": hours})
    biz: dict[str, Any] = {"@context": "https://schema.org", "@type": SCHEMA_TYPES.get(lead.get("category") or "", "LocalBusiness"),
                           "name": name}
    if phone:
        biz["telephone"] = phone
    if where:
        biz["address"] = {"@type": "PostalAddress", "streetAddress": lead.get("address") or "", "addressLocality": city}
    if hours:
        biz["openingHours"] = hours
    if (lead.get("extra") or {}).get("cuisine"):
        biz["servesCuisine"] = lead["extra"]["cuisine"].replace(";", ", ")
    graph = [biz]
    if faq:
        graph.append({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in faq]})
    return faq, json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")


def render_mockup(lead: dict[str, Any], cfg: dict[str, Any], photos: list[str] | None = None, logo: str = "") -> str:
    spec = mockup_spec(lead, cfg)
    lang = spec["lang"]
    m = market(cfg, lead.get("market"))
    layout = "photo" if photos else spec["layout"]
    hours = (lead.get("extra") or {}).get("opening_hours")
    faq, jsonld = geo_markup(lead, cfg, lang, hours)
    return _env.get_template("mockups/onepage.html").render(
        lead=lead, spec=spec, layout=layout, photos=photos or [], logo=logo, lang=lang, t=STRINGS[lang],
        hours=hours, faq=faq, jsonld=jsonld,
        whatsapp=whatsapp_number(lead.get("phone"), m.get("country_code")),
        business=cfg["business"],
    )


AUDIT_STRINGS = {
    "fr": {
        "title": "Audit SEO & GEO", "kicker": "Audit SEO & GEO", "h1": "Visibilité Google et IA —",
        "lede": "Ce que Google et les assistants IA (ChatGPT, Perplexity, Gemini) comprennent aujourd'hui de votre commerce, et ce qu'il faut corriger.",
        "why": "Le GEO (Generative Engine Optimization) est le nouveau SEO : de plus en plus de clients demandent directement à une IA « quel est le meilleur {cat} à {city} ? ». Les IA citent les commerces dont le site est clair, structuré et vérifiable. Chaque point ci-dessous a été constaté sur votre site le {today}.",
        "why_none": "Le GEO (Generative Engine Optimization) est le nouveau SEO : de plus en plus de clients demandent directement à une IA « quel est le meilleur {cat} à {city} ? ». Les IA s'appuient sur des sites clairs et structurés ; sans site trouvé à votre nom, elles n'ont aucune source officielle sur vous.",
        "why_down": "Le GEO (Generative Engine Optimization) est le nouveau SEO : de plus en plus de clients demandent directement à une IA « quel est le meilleur {cat} à {city} ? ». Les IA s'appuient sur des sites clairs et structurés ; tant que votre site ne s'affiche plus, elles ne peuvent pas vous citer.",
        "findings_title": "Nos recommandations, par ordre d'impact", "fix": "À faire :",
        "fixed_by_redesign": "corrigé par la refonte (site rapide, mobile, sécurisé).",
        "good_title": "Ce qui est déjà en place",
        "cta": "Toutes ces recommandations sont mises en place dans le cadre de la refonte de votre site. Votre maquette est déjà prête : répondez « OUI » et je la mets en ligne à votre nom. Vous ne réglez qu'une fois le site en ligne et validé.",
        "cta_geo": "Ce rapport liste ce que nous avons constaté. L'{label} ({price}, livré en {delivery}) va plus loin : pages clés, fiche Google, comparaison avec 3 concurrents locaux, cohérence de vos coordonnées sur le web et plan d'action priorisé, présenté en 30 min. Répondez « OUI » à mon email : vous ne réglez qu'à la livraison.",
        "see_mockup": "Voir ma maquette", "email": "Répondre par email",
        "footer": "Audit réalisé automatiquement à partir de votre page publique ; aucune donnée personnelle collectée.",
        "passed": {"schema_local": "Données structurées de commerce local", "schema_faq": "FAQ structurée", "nap": "Nom, adresse et téléphone lisibles",
                   "hours": "Horaires publiés", "meta_description": "Description pour Google", "title": "Titre de page bien calibré", "h1": "Titre principal (H1)",
                   "lang": "Langue déclarée", "canonical": "URL canonique", "og": "Aperçu de partage", "content_depth": "Contenu suffisant",
                   "ai_crawlers": "Robots des IA autorisés", "sitemap": "Plan du site (sitemap.xml)", "llms_txt": "Fichier llms.txt"},
    },
    "en": {
        "title": "SEO & GEO audit", "kicker": "SEO & GEO audit", "h1": "Google & AI visibility —",
        "lede": "What Google and AI assistants (ChatGPT, Perplexity, Gemini) understand about your business today, and what to fix.",
        "why": "GEO (Generative Engine Optimization) is the new SEO: more and more customers ask an AI directly \"what's the best {cat} in {city}?\". AI assistants cite businesses whose website is clear, structured and verifiable. Every point below was checked on your site on {today}.",
        "why_none": "GEO (Generative Engine Optimization) is the new SEO: more and more customers ask an AI directly \"what's the best {cat} in {city}?\". AI assistants rely on clear, structured websites; with no website found under your name, they have no official source about you.",
        "why_down": "GEO (Generative Engine Optimization) is the new SEO: more and more customers ask an AI directly \"what's the best {cat} in {city}?\". AI assistants rely on clear, structured websites; while your site no longer loads, they can't cite you.",
        "findings_title": "Our recommendations, by impact", "fix": "To do:",
        "fixed_by_redesign": "fixed by the redesign (fast, mobile, secure site).",
        "good_title": "Already in place",
        "cta": "All these recommendations are implemented as part of your website redesign. Your mock-up is ready: reply \"YES\" and I'll put it live under your name. You only pay once the site is live and approved.",
        "cta_geo": "This report lists what we found. The {label} ({price}, delivered in {delivery}) goes further: key pages, Google profile, benchmark against 3 local competitors, consistency of your details across the web and a prioritised action plan, walked through in 30 minutes. Reply \"YES\" to my email: you only pay on delivery.",
        "see_mockup": "See my mock-up", "email": "Reply by email",
        "footer": "Audit run automatically on your public page; no personal data collected.",
        "passed": {"schema_local": "Local-business structured data", "schema_faq": "Structured FAQ", "nap": "Readable name, address and phone",
                   "hours": "Opening hours published", "meta_description": "Description for Google", "title": "Well-sized page title", "h1": "Main heading (H1)",
                   "lang": "Language declared", "canonical": "Canonical URL", "og": "Sharing preview", "content_depth": "Enough content",
                   "ai_crawlers": "AI crawlers allowed", "sitemap": "Sitemap", "llms_txt": "llms.txt file"},
    },
}


def render_audit(lead: dict[str, Any], cfg: dict[str, Any], mockup_url: str | None = None) -> str:
    """Rapport « Audit SEO & GEO » : faits constatés + recommandations, mis en œuvre dans la refonte."""
    from datetime import date
    from .audit import geo_findings, geo_score, issue_label
    lang = market(cfg, lead.get("market")).get("language", "fr")
    t = dict(AUDIT_STRINGS.get(lang, AUDIT_STRINGS["fr"]))
    extra = lead.get("extra") or {}
    geo = extra.get("geo")
    codes = [i["code"] for i in lead.get("issues") or []]
    has_site = bool(lead.get("website")) and bool(geo)
    site_down = bool(lead.get("website")) and bool({"down", "expired", "parked", "listing"} & set(codes))
    cat = cfg["prospecting"]["categories"].get(lead.get("category") or "", {}).get(lang) or ("commerce" if lang == "fr" else "business")
    today = date.today().strftime("%d/%m/%Y" if lang == "fr" else "%d %b %Y")
    t["why"] = t["why" if has_site else "why_down" if site_down else "why_none"].format(cat=cat, city=lead.get("city") or "", today=today)
    findings = geo_findings(geo, lang, has_site=has_site, site_down=site_down)
    site_issues = [issue_label(c, i.get("params", {}), lang) for c, i in ((i["code"], i) for i in lead.get("issues") or [])
                   if c not in ("no_site",)] if lead.get("website") and not site_down else []
    passed = [t["passed"][g["code"]] for g in geo or [] if g.get("ok") and g["code"] in t["passed"]]
    score = geo_score(geo) if has_site else 0
    color = "#c92a2a" if (score or 0) < 40 else "#b7791f" if score < 70 else "#2f7d4f"
    if extra.get("offer") == "geo":
        g, m = cfg["pricing"]["geo_audit"], market(cfg, lead.get("market"))
        t["cta"] = t["cta_geo"].format(label=g["label"][lang], delivery=g["delivery"][lang],
                                       price=format_price(m["prices"]["geo"], m))
        mockup_url = None
    return _env.get_template("mockups/audit.html").render(
        lang=lang, t=t, lead=lead, business=cfg["business"], today=today, geo_score=score, score_color=color,
        findings=findings, site_issues=site_issues[:4], passed=passed, mockup_url=mockup_url)


def write_mockup(lead: dict[str, Any], cfg: dict[str, Any]) -> tuple[str, str | None]:
    """Écrit la maquette sur disque. Retourne (chemin local, URL publique éventuelle)."""
    slug = mockup_slug(lead)
    out = Path(cfg["paths"]["mockups"]) / slug
    out.mkdir(parents=True, exist_ok=True)
    spec = mockup_spec(lead, cfg)
    photos, logo = download_assets(spec, out) if (spec["photos"] or spec["logo"]) else ([], "")
    (out / "index.html").write_text(render_mockup(lead, cfg, photos, logo), encoding="utf-8")
    base = (cfg["business"].get("mockup_base_url") or "").rstrip("/")
    url = f"{base}/{slug}/" if base else None
    (out / "audit").mkdir(exist_ok=True)
    (out / "audit" / "index.html").write_text(render_audit(lead, cfg, url), encoding="utf-8")
    return str(out / "index.html"), url


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
    """Capture (preview.jpg, format mobile) de chaque maquette qui n'en a pas encore.

    Utilise Playwright côté Node (préinstallé dans l'environnement cloud). Sans Node/Playwright,
    les emails partent simplement sans image.
    """
    jobs = [[str(Path(h).resolve()), str(Path(h).resolve().with_name("preview.jpg"))]
            for h in html_paths if not Path(h).with_name("preview.jpg").exists()]
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
    return Path(cfg["paths"]["mockups"]) / mockup_slug(lead) / "preview.jpg"
