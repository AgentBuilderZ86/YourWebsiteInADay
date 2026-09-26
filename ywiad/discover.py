"""Découverte de commerçants : OpenStreetMap (Overpass), Google Places, ou import CSV."""

from __future__ import annotations

import csv
import os
from typing import Any, Iterator

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
USER_AGENT = "YourWebsiteInADay/0.1 (+prospection commerçants)"


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.lower().startswith(("http://", "https://")):
        url = "http://" + url
    return url.rstrip("/")


def _is_social(url: str | None) -> bool:
    """Une page Facebook/Instagram n'est pas un site : on la traite comme "sans site"."""
    return bool(url) and any(
        d in url.lower() for d in ("facebook.com", "instagram.com", "tiktok.com", "linktr.ee", "wa.me")
    )


def _clean_lead(lead: dict[str, Any]) -> dict[str, Any]:
    website = normalize_url(lead.get("website"))
    if _is_social(website):
        lead.setdefault("extra", {})["social"] = website
        website = None
    lead["website"] = website
    return lead


# --- OpenStreetMap -------------------------------------------------------

def build_overpass_query(area: str, osm_tag: str) -> str:
    key, value = osm_tag.split("=", 1)
    return f"""
[out:json][timeout:60];
area["name"="{area}"]->.a;
(
  node["{key}"="{value}"]["name"](area.a);
  way["{key}"="{value}"]["name"](area.a);
);
out center tags;
"""


def parse_overpass(payload: dict[str, Any], category: str, city: str) -> Iterator[dict[str, Any]]:
    for el in payload.get("elements", []):
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        address = " ".join(
            p for p in (tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:city")) if p
        )
        yield _clean_lead({
            "source": "osm",
            "source_id": f"{el.get('type')}/{el.get('id')}",
            "name": name,
            "category": category,
            "website": tags.get("website") or tags.get("contact:website") or tags.get("url"),
            "email": tags.get("email") or tags.get("contact:email"),
            "phone": tags.get("phone") or tags.get("contact:phone"),
            "address": address or None,
            "city": tags.get("addr:city") or city,
            "extra": {
                k: tags[k] for k in ("opening_hours", "cuisine", "facebook", "instagram") if k in tags
            },
        })


def discover_osm(area: str, categories: dict[str, dict[str, str]], session: requests.Session | None = None) -> Iterator[dict[str, Any]]:
    s = session or requests.Session()
    for category, spec in categories.items():
        if not spec.get("osm"):
            continue
        resp = s.post(
            OVERPASS_URL,
            data={"data": build_overpass_query(area, spec["osm"])},
            headers={"User-Agent": USER_AGENT},
            timeout=90,
        )
        resp.raise_for_status()
        yield from parse_overpass(resp.json(), category, area)


# --- Google Places (API New) --------------------------------------------

def parse_places(payload: dict[str, Any], category: str, city: str) -> Iterator[dict[str, Any]]:
    for p in payload.get("places", []):
        yield _clean_lead({
            "source": "google",
            "source_id": p["id"],
            "name": p.get("displayName", {}).get("text", "?"),
            "category": category,
            "website": p.get("websiteUri"),
            "email": None,
            "phone": p.get("internationalPhoneNumber") or p.get("nationalPhoneNumber"),
            "address": p.get("formattedAddress"),
            "city": city,
            "extra": {"rating": p.get("rating"), "reviews": p.get("userRatingCount")},
        })


def discover_google(area: str, categories: dict[str, dict[str, str]], session: requests.Session | None = None) -> Iterator[dict[str, Any]]:
    key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY manquante pour la source 'google'")
    s = session or requests.Session()
    fields = ",".join(f"places.{f}" for f in (
        "id", "displayName", "websiteUri", "nationalPhoneNumber", "internationalPhoneNumber",
        "formattedAddress", "rating", "userRatingCount",
    ))
    for category, spec in categories.items():
        if not spec.get("google"):
            continue
        resp = s.post(
            PLACES_URL,
            json={"textQuery": f"{spec['google']} {area}", "pageSize": 20, "languageCode": "fr"},
            headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": fields},
            timeout=30,
        )
        resp.raise_for_status()
        yield from parse_places(resp.json(), category, area)


# --- CSV -----------------------------------------------------------------

def import_csv(path: str) -> Iterator[dict[str, Any]]:
    """Colonnes attendues : name, category, website, email, phone, address, city."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            if not row.get("name"):
                continue
            yield _clean_lead({
                "source": "csv",
                "source_id": row.get("id") or f"{path}:{i}",
                "name": row["name"].strip(),
                "category": (row.get("category") or "").strip() or None,
                "website": row.get("website"),
                "email": (row.get("email") or "").strip() or None,
                "phone": (row.get("phone") or "").strip() or None,
                "address": (row.get("address") or "").strip() or None,
                "city": (row.get("city") or "").strip() or None,
                "extra": {},
            })


def discover(cfg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    p = cfg["prospecting"]
    for source in p.get("sources", ["osm"]):
        if source == "osm":
            yield from discover_osm(p["area"], p["categories"])
        elif source == "google":
            yield from discover_google(p["area"], p["categories"])
