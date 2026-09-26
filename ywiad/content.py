"""Extraction du contenu réel d'un site (textes, rubriques, photos, logo, couleur) pour une maquette de refonte."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

# Page d'hébergeur, de revente ou d'enchères : le nom de domaine n'héberge plus le site du commerce
PARKING_MARKERS = (
    "domain is for sale", "this domain may be for sale", "buy this domain", "ce domaine est à vendre",
    "nom de domaine est à vendre", "ce nom de domaine est en vente", "parked domain", "domain parking",
    "highest bidder", "registered on behalf of", "domain name registration", "is run by",
    "sedoparking", "dan.com", "afternic", "hugedomains", "domain has expired", "ce domaine a expiré",
    "welcome to nginx", "apache2 ubuntu default page", "default web site page", "future home of",
    "hosting services", "cette page est générée automatiquement", "page par défaut",
    "want a domain name like this", "a été supprimé", "has been deleted", "site has been removed",
    "this site is no longer available", "ce site n'est plus disponible", "account suspended", "compte suspendu",
    "site désactivé", "this account has been suspended", "website expired", "site web a expiré",
)
# Pages de vérification anti-robot : le vrai contenu n'est pas visible, l'audit n'est pas fiable
CHALLENGE_MARKERS = ("sgcaptcha", "cf-browser-verification", "cf-challenge", "just a moment...",
                     "checking your browser", "ddos-guard", "captcha-delivery", "/cdn-cgi/challenge-platform")
IMG_URL_RE = re.compile(r"""(?:https?:)?//?[^\s"'()<>,]+?\.(?:jpe?g|png|webp)(?:\?[^\s"'()<>,]*)?""", re.I)


def is_challenge_page(html: str) -> bool:
    low = html[:20000].lower()
    return any(m in low for m in CHALLENGE_MARKERS)
GENERIC_HEADLINES = {"accueil", "home", "welcome", "bienvenue", "index", "page d'accueil", "menu"}
SKIP_IMG = ("logo", "icon", "sprite", "pixel", "flag", "badge", "avatar", "loader", "spinner", "facebook",
            "instagram", "twitter", "whatsapp", "tripadvisor", "payment", "visa", "mastercard", "language-")


def is_parking_page(title: str, text: str, host: str) -> bool:
    t = (title + " " + text[:3000]).lower()
    if any(m in t for m in PARKING_MARKERS):
        return True
    bare = host.lower().removeprefix("www.")
    # Titre = nom de domaine nu, avec très peu de contenu : page par défaut d'hébergeur
    return bool(bare) and title.strip().lower().removeprefix("www.") == bare and len(text) < 2500


class _ContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.h1: list[str] = []
        self.sections: list[dict[str, str]] = []
        self.paragraphs: list[str] = []
        self.images: list[dict[str, str]] = []
        self.icons: list[str] = []
        self._stack: list[str] = []
        self._buf: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript", "nav", "footer", "header", "form"):
            self._skip += 1
        if tag == "meta":
            key = (a.get("name") or a.get("property") or "").lower()
            if key:
                self.meta[key] = a.get("content", "")
        elif tag == "link" and "icon" in a.get("rel", "").lower() and a.get("href"):
            self.icons.append(a["href"])
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or a.get("data-lazy-src") or ""
            if src and not src.startswith("data:"):
                self.images.append({"src": src, "alt": a.get("alt", ""), "cls": a.get("class", "") + a.get("id", ""),
                                    "width": a.get("width", "")})
        elif tag in ("title", "h1", "h2", "h3", "p"):
            self._stack.append(tag)
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript", "nav", "footer", "header", "form") and self._skip:
            self._skip -= 1
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
            text = " ".join("".join(self._buf).split())
            if tag == "title":
                self.title = text
            elif self._skip:
                pass
            elif tag == "h1" and text:
                self.h1.append(text)
            elif tag in ("h2", "h3") and 3 <= len(text) <= 60:
                self.sections.append({"title": text, "text": ""})
            elif tag == "p" and len(text) >= 40:
                self.paragraphs.append(text)
                if self.sections and not self.sections[-1]["text"]:
                    self.sections[-1]["text"] = text

    def handle_data(self, data: str) -> None:
        if self._stack:
            self._buf.append(data)


BOILERPLATE_RE = re.compile(
    r"\b(book now|read more|learn more|click here|en savoir plus|lire la suite|réserver maintenant|"
    r"réservez maintenant|voir plus|see more|découvrir|discover more)\b", re.I)
GENERIC_SECTIONS = {"gallery", "galerie", "services", "nos services", "contact", "contactez-nous", "about", "about us",
                    "à propos", "a propos", "menu", "photos", "news", "actualités", "blog", "faq", "testimonials", "avis"}


def _clean(text: str) -> str:
    """Retire les boutons de slider (« Book Now »…) et les doublons de phrases collées."""
    text = BOILERPLATE_RE.sub(" · ", text)
    parts = [p.strip(" ·-–—") for p in re.split(r"\s*·\s*", text) if p.strip(" ·-–—")]
    seen, out = set(), []
    for part in parts:
        if part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    return ". ".join(out) if len(out) > 1 else (out[0] if out else "")


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    return cut.rstrip(",;:—-") + "…"


def extract_site_content(html: str, base_url: str) -> dict[str, Any]:
    p = _ContentParser()
    try:
        p.feed(html)
    except Exception:
        pass
    abs_url = lambda u: urljoin(base_url, u)  # noqa: E731
    host = urlparse(base_url).netloc

    headline = next((h for h in p.h1 if 6 <= len(h) <= 70 and h.lower() not in GENERIC_HEADLINES), "")
    description = _clean(p.meta.get("description") or p.meta.get("og:description") or (p.paragraphs[0] if p.paragraphs else ""))

    logo = ""
    photos: list[str] = []
    for img in p.images:
        src = abs_url(img["src"])
        marker = (img["src"] + img["cls"] + img["alt"]).lower()
        if not logo and "logo" in marker:
            logo = src
            continue
        if src.lower().split("?")[0].endswith((".svg", ".gif", ".ico")) or any(s in marker for s in SKIP_IMG):
            continue
        if img["width"].isdigit() and int(img["width"]) < 200:
            continue
        if src not in photos:
            photos.append(src)
    # Images en lazy-load, srcset ou fond CSS : on les repère directement dans le code
    for raw in IMG_URL_RE.findall(html):
        src = abs_url(raw if not raw.startswith("//") else "https:" + raw)
        low = src.lower()
        if any(s in low for s in SKIP_IMG) or re.search(r"-(\d{2,3})x(\d{2,3})\.", low) and int(re.search(r"-(\d{2,3})x", low).group(1)) < 300:
            continue
        if not logo and "logo" in low:
            logo = src
            continue
        if src not in photos and urlparse(src).netloc:
            photos.append(src)
    og = p.meta.get("og:image")
    if og and abs_url(og) not in photos:
        photos.insert(0, abs_url(og))

    color = p.meta.get("theme-color", "").strip()
    seen: set[str] = set()
    sections = []
    for s in p.sections:
        key = s["title"].lower()
        if key in seen or key in GENERIC_HEADLINES or key in GENERIC_SECTIONS or not s["text"]:
            continue
        seen.add(key)
        sections.append({"title": _clip(_clean(s["title"]), 40), "text": _clip(_clean(s["text"]), 160)})
    if len(sections) < 2:
        sections = []  # trop peu de contenu réel : les rubriques génériques sont plus propres

    return {
        "host": host,
        "title": _clip(p.title, 80),
        "headline": _clip(headline, 70),
        "description": _clip(description, 200),
        "sections": sections[:4],
        "photos": photos[:8],
        "logo": logo,
        "theme_color": color if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else "",
        "parked": is_parking_page(p.title, " ".join(p.paragraphs + p.h1 + [s["title"] for s in p.sections]), host),
    }
