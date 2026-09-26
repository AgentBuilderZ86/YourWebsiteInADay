"""Découverte de commerçants dans le monde : OpenStreetMap (Overpass), Google Places, ou import CSV."""

from __future__ import annotations

import csv
import itertools
import logging
import os
import random
import time
from typing import Any, Iterator
from urllib.parse import urlparse

import requests

log = logging.getLogger("ywiad")

# Plusieurs serveurs Overpass : certains sont saturés ou bloqués selon le réseau
OVERPASS_URLS = (
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
USER_AGENT = "YourWebsiteInADay/0.2 (+prospection commerçants)"
CHAIN_TAGS = ("brand", "brand:wikidata", "operator:wikidata")
SOCIAL_DOMAINS = ("facebook.com", "instagram.com", "tiktok.com", "linktr.ee", "wa.me", "linkedin.com",
                  "tripadvisor.", "booking.com", "yelp.", "pagesjaunes.", "treatwell.", "planity.", "doctolib.")


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip().split(";")[0].strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "http://" + url
    return url.rstrip("/")


def _is_social(url: str | None) -> bool:
    """Une page Facebook, un annuaire ou une plateforme de réservation n'est pas un site propre."""
    return bool(url) and any(d in url.lower() for d in SOCIAL_DOMAINS)


ROOT_LIKE_SEGMENTS = {"fr", "en", "ar", "es", "home", "accueil", "index.html", "index.php", "fr-fr", "en-us"}


def is_network_page(url: str | None) -> bool:
    """Page d'agence sur le site d'un réseau/franchise (ex. orpi.com/mon-agence) : le commerçant ne maîtrise pas son site."""
    if not url:
        return False
    segments = [s for s in urlparse(url).path.split("/") if s]
    return bool(segments) and not (len(segments) == 1 and segments[0].lower() in ROOT_LIKE_SEGMENTS)


def _clean_lead(lead: dict[str, Any]) -> dict[str, Any]:
    website = normalize_url(lead.get("website"))
    if _is_social(website):
        lead.setdefault("extra", {})["social"] = website
        website = None
    elif is_network_page(website):
        lead.setdefault("extra", {})["network"] = website
    lead["website"] = website
    if lead.get("email"):
        lead["email"] = lead["email"].split(";")[0].strip().lower() or None
    return lead


# --- Rotation des cibles -------------------------------------------------

def all_combos(cfg: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Toutes les combinaisons (marché, ville, catégorie), dans un ordre mélangé mais stable."""
    combos = [
        (code, city, cat)
        for code, m in cfg["markets"].items() if m.get("enabled")
        for city, cat in itertools.product(m.get("cities", []), cfg["prospecting"]["categories"])
    ]
    random.Random(42).shuffle(combos)
    return combos


def next_combos(cfg: dict[str, Any], cursor: int, n: int) -> tuple[list[tuple[str, str, str]], int]:
    combos = all_combos(cfg)
    if not combos:
        return [], cursor
    picked = [combos[(cursor + i) % len(combos)] for i in range(min(n, len(combos)))]
    return picked, (cursor + len(picked)) % len(combos)


# --- OpenStreetMap -------------------------------------------------------

def build_overpass_query(country_code: str, city: str, osm_tag: str) -> str:
    """La ville est résolue par Overpass lui-même (limite administrative portant ce nom, dans le pays)."""
    key, value = osm_tag.split("=", 1)
    city = city.replace('"', "")
    return f"""
[out:json][timeout:120];
area["ISO3166-1"="{country_code.upper()}"][admin_level=2]->.c;
(
  rel(area.c)["boundary"="administrative"]["name"="{city}"];
  rel(area.c)["boundary"="administrative"]["name:en"="{city}"];
  rel(area.c)["boundary"="administrative"]["name:fr"="{city}"];
);
map_to_area->.a;
(
  node["{key}"="{value}"]["name"](area.a);
  way["{key}"="{value}"]["name"](area.a);
);
out center tags;
"""


def overpass(query: str, session: requests.Session) -> dict[str, Any]:
    last: Exception | None = None
    for attempt, url in enumerate(OVERPASS_URLS * 2):
        try:
            resp = session.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=150)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.info("Overpass %s indisponible : %s", url, exc)
            last = exc
            time.sleep(min(30, 5 * (attempt + 1)))
    raise RuntimeError(f"Aucun serveur Overpass disponible ({last})")


def parse_overpass(payload: dict[str, Any], category: str, city: str, market: str | None = None) -> Iterator[dict[str, Any]]:
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name or any(t in tags for t in CHAIN_TAGS):
            continue  # pas de nom, ou chaîne / franchise : hors cible
        address = " ".join(
            p for p in (tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:city")) if p
        )
        yield _clean_lead({
            "source": "osm",
            "source_id": f"{el.get('type')}/{el.get('id')}",
            "name": name,
            "category": category,
            "market": market,
            "website": tags.get("website") or tags.get("contact:website") or tags.get("url"),
            "email": tags.get("email") or tags.get("contact:email"),
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "address": address or None,
            "city": tags.get("addr:city") or city,
            "extra": {
                k: tags[k] for k in ("opening_hours", "cuisine", "facebook", "instagram", "stars") if k in tags
            },
        })


def discover_osm(cfg: dict[str, Any], combos: list[tuple[str, str, str]],
                 session: requests.Session | None = None) -> Iterator[dict[str, Any]]:
    s = session or requests.Session()
    cats = cfg["prospecting"]["categories"]
    for code, city, cat in combos:
        cc = cfg["markets"][code].get("country_code", code)
        log.info("recherche %s à %s (%s)", cat, city, code)
        try:
            payload = overpass(build_overpass_query(cc, city, cats[cat]["osm"]), s)
        except RuntimeError as exc:
            log.warning("%s à %s ignoré : %s", cat, city, exc)
            continue
        yield from parse_overpass(payload, cat, city, code)


# --- Google Places (API New) --------------------------------------------

def parse_places(payload: dict[str, Any], category: str, city: str, market: str | None = None) -> Iterator[dict[str, Any]]:
    for p in payload.get("places", []):
        yield _clean_lead({
            "source": "google",
            "source_id": p["id"],
            "name": p.get("displayName", {}).get("text", "?"),
            "category": category,
            "market": market,
            "website": p.get("websiteUri"),
            "email": None,
            "phone": p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber"),
            "address": p.get("formattedAddress"),
            "city": city,
            "extra": {"rating": p.get("rating"), "reviews": p.get("userRatingCount")},
        })


def discover_google(cfg: dict[str, Any], combos: list[tuple[str, str, str]],
                    session: requests.Session | None = None) -> Iterator[dict[str, Any]]:
    key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY manquante pour la source 'google'")
    s = session or requests.Session()
    fields = ",".join(f"places.{f}" for f in (
        "id", "displayName", "websiteUri", "nationalPhoneNumber", "internationalPhoneNumber",
        "formattedAddress", "rating", "userRatingCount",
    ))
    cats = cfg["prospecting"]["categories"]
    for code, city, cat in combos:
        lang = cfg["markets"][code].get("language", "fr")
        resp = s.post(
            PLACES_URL,
            json={"textQuery": f"{cats[cat][lang]} {city}", "pageSize": 20, "languageCode": lang,
                  "regionCode": cfg["markets"][code].get("country_code", code)},
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": fields},
            timeout=30,
        )
        resp.raise_for_status()
        yield from parse_places(resp.json(), cat, city, code)


# --- CSV -----------------------------------------------------------------

def import_csv(path: str) -> Iterator[dict[str, Any]]:
    """Colonnes : name, category, market, website, email, phone, address, city."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            if not row.get("name"):
                continue
            yield _clean_lead({
                "source": "csv",
                "source_id": row.get("id") or f"{path}:{i}",
                "name": row["name"].strip(),
                "category": (row.get("category") or "").strip() or None,
                "market": (row.get("market") or "").strip().upper() or None,
                "website": row.get("website"),
                "email": (row.get("email") or "").strip() or None,
                "phone": (row.get("phone") or "").strip() or None,
                "address": (row.get("address") or "").strip() or None,
                "city": (row.get("city") or "").strip() or None,
                "extra": {},
            })


def discover(cfg: dict[str, Any], combos: list[tuple[str, str, str]]) -> Iterator[dict[str, Any]]:
    for source in cfg["prospecting"].get("sources", ["osm"]):
        if source == "osm":
            yield from discover_osm(cfg, combos)
        elif source == "google":
            yield from discover_google(cfg, combos)
