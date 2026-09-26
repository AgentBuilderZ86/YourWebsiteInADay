"""Audit automatique d'un site web : score /100 + liste de problèmes compréhensibles par un commerçant."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Any

import requests

from .discover import USER_AGENT

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IGNORED_EMAIL_PARTS = ("example.", "sentry", "wixpress", "@2x", ".png", ".jpg", "domain.com")
PARKED_MARKERS = (
    "domain is for sale", "ce domaine est à vendre", "under construction", "en construction",
    "coming soon", "site en maintenance", "parked domain", "buy this domain",
)


@dataclass
class Issue:
    code: str
    label: str      # phrase destinée au commerçant
    penalty: int


@dataclass
class AuditResult:
    url: str | None
    score: int
    issues: list[Issue] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    load_seconds: float | None = None
    reachable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "score": self.score,
            "reachable": self.reachable,
            "load_seconds": self.load_seconds,
            "issues": [i.__dict__ for i in self.issues],
            "emails": self.emails,
        }


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.h1 = 0
        self.imgs = 0
        self.imgs_no_alt = 0
        self.tables = 0
        self.legacy_tags: set[str] = set()
        self.scripts: list[str] = []
        self.links: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name:
                self.meta[name] = a.get("content", "")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "img":
            self.imgs += 1
            if not a.get("alt", "").strip():
                self.imgs_no_alt += 1
        elif tag == "table":
            self.tables += 1
        elif tag in ("marquee", "font", "frameset", "frame", "center", "blink"):
            self.legacy_tags.add(tag)
        elif tag in ("embed", "object") and "flash" in (a.get("type", "") + a.get("src", "") + a.get("data", "")).lower():
            self.legacy_tags.add("flash")
        elif tag == "script" and a.get("src"):
            self.scripts.append(a["src"])
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        self.text_parts.append(data)

    @property
    def text(self) -> str:
        return " ".join(self.text_parts)


def analyze_html(html: str, *, final_url: str, load_seconds: float, page_bytes: int,
                 https_ok: bool, ssl_valid: bool, today: date | None = None) -> AuditResult:
    """Analyse pure (sans réseau) — testable unitairement."""
    today = today or date.today()
    p = _PageParser()
    try:
        p.feed(html)
    except Exception:  # HTML très cassé
        pass
    text_lower = p.text.lower()
    issues: list[Issue] = []

    def add(code: str, label: str, penalty: int) -> None:
        issues.append(Issue(code, label, penalty))

    if any(m in text_lower for m in PARKED_MARKERS) or len(p.text.strip()) < 200:
        add("empty", "Le site est vide, en construction ou quasi sans contenu", 30)
    if not https_ok:
        add("no_https", "Le site n'est pas sécurisé (pas de HTTPS) : Chrome affiche « Non sécurisé »", 20)
    elif not ssl_valid:
        add("bad_ssl", "Le certificat de sécurité est invalide : les visiteurs voient une alerte", 20)
    if "viewport" not in p.meta:
        add("not_mobile", "Le site n'est pas adapté aux smartphones (70 % des visites)", 20)
    if load_seconds > 6:
        add("very_slow", f"Chargement très lent ({load_seconds:.1f} s) : la moitié des visiteurs partent", 15)
    elif load_seconds > 3:
        add("slow", f"Chargement lent ({load_seconds:.1f} s)", 8)
    if page_bytes > 3_000_000:
        add("heavy", f"Page très lourde ({page_bytes / 1e6:.1f} Mo)", 5)
    if not p.title.strip():
        add("no_title", "Pas de titre de page : invisible dans les résultats Google", 6)
    if not p.meta.get("description", "").strip():
        add("no_description", "Pas de description Google : le résultat de recherche est peu attractif", 5)
    if p.h1 == 0:
        add("no_h1", "Structure de page non optimisée pour le référencement", 3)
    if p.legacy_tags:
        add("legacy_tech", "Technologies obsolètes (" + ", ".join(sorted(p.legacy_tags)) + ")", 10)
    if any(re.search(r"jquery[-.]?1\.\d", s, re.I) for s in p.scripts):
        add("old_jquery", "Bibliothèques techniques anciennes (failles de sécurité possibles)", 5)
    if p.tables > 5:
        add("table_layout", "Mise en page datée (construite en tableaux)", 5)
    if p.imgs and p.imgs_no_alt / p.imgs > 0.5:
        add("img_alt", "Images non décrites : perte de référencement Google Images", 3)
    if "og:title" not in p.meta and "og:image" not in p.meta:
        add("no_og", "Aperçu non optimisé quand le lien est partagé sur WhatsApp / Facebook", 3)
    if not any(h.startswith(("tel:", "mailto:")) or "wa.me" in h or "whatsapp" in h for h in p.links):
        add("no_cta", "Pas de bouton d'appel / WhatsApp : le client ne peut pas vous contacter en 1 clic", 5)
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?((?:19|20)\d{2})", text_lower)]
    if years and max(years) <= today.year - 3:
        add("stale", f"Site non mis à jour depuis {max(years)}", 10)

    emails = sorted({
        e.lower() for e in EMAIL_RE.findall(html)
        if not any(x in e.lower() for x in IGNORED_EMAIL_PARTS)
    })
    score = max(0, 100 - sum(i.penalty for i in issues))
    issues.sort(key=lambda i: -i.penalty)
    return AuditResult(final_url, score, issues, emails, round(load_seconds, 2))


def _pagespeed_mobile_score(url: str) -> int | None:
    key = os.environ.get("PAGESPEED_API_KEY")
    if not key:
        return None
    try:
        r = requests.get(
            "https://www.googleapis.com/pagespeedonline/v5/runPagespeed",
            params={"url": url, "strategy": "mobile", "key": key, "category": "performance"},
            timeout=90,
        )
        r.raise_for_status()
        return round(r.json()["lighthouseResult"]["categories"]["performance"]["score"] * 100)
    except Exception:
        return None


def audit_url(url: str | None, *, timeout: int = 15, use_pagespeed: bool = False) -> AuditResult:
    if not url:
        return AuditResult(None, 0, [Issue("no_site", "Aucun site web : vos clients ne vous trouvent pas sur Google", 100)],
                           reachable=False)
    headers = {"User-Agent": USER_AGENT + " Mozilla/5.0"}
    https_url = re.sub(r"^http://", "https://", url)
    ssl_valid = True
    start = time.monotonic()
    resp = None
    try:
        resp = requests.get(https_url, headers=headers, timeout=timeout, allow_redirects=True)
    except requests.exceptions.SSLError:
        ssl_valid = False
        try:
            resp = requests.get(https_url, headers=headers, timeout=timeout, verify=False)  # noqa: S501
        except requests.RequestException:
            resp = None
    except requests.RequestException:
        resp = None
    if resp is None:
        # Pas de HTTPS du tout : on retente en HTTP
        try:
            start = time.monotonic()
            resp = requests.get(re.sub(r"^https://", "http://", url), headers=headers, timeout=timeout)
        except requests.RequestException:
            return AuditResult(url, 0, [Issue("down", "Le site est inaccessible (erreur ou domaine expiré)", 100)],
                               reachable=False)
    elapsed = time.monotonic() - start
    if resp.status_code >= 400:
        return AuditResult(url, 0, [Issue("down", f"Le site renvoie une erreur ({resp.status_code})", 100)],
                           reachable=False)
    result = analyze_html(
        resp.text,
        final_url=resp.url,
        load_seconds=elapsed,
        page_bytes=len(resp.content),
        https_ok=resp.url.startswith("https://"),
        ssl_valid=ssl_valid,
    )
    if use_pagespeed:
        ps = _pagespeed_mobile_score(resp.url)
        if ps is not None and ps < 50:
            result.issues.append(Issue("pagespeed", f"Score de performance mobile Google : {ps}/100", 10))
            result.score = max(0, result.score - 10)
    return result
