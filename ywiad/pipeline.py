"""Orchestration de la routine quotidienne : découverte → audit → maquette → contact → relances → rapport."""

from __future__ import annotations

import csv
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .audit import audit_url
from .db import DB, now_iso
from .discover import discover, import_csv
from .mailer import Mailer, fetch_replies
from .mockup import write_mockup
from .outreach import is_optout_reply, next_followup, render_email, render_whatsapp
from .pricing import format_price, recommend_tier

log = logging.getLogger("ywiad")


def ingest(db: DB, leads: Iterable[dict[str, Any]], limit: int) -> int:
    created = 0
    for lead in leads:
        _, is_new = db.upsert_lead(lead)
        created += is_new
        if created >= limit:
            break
    return created


def step_discover(db: DB, cfg: dict[str, Any]) -> int:
    return ingest(db, discover(cfg), cfg["prospecting"].get("max_leads_per_run", 50))


def step_import(db: DB, path: str) -> int:
    return ingest(db, import_csv(path), 10**9)


def step_audit(db: DB, cfg: dict[str, Any], auditor: Callable[..., Any] = audit_url) -> dict[str, int]:
    a = cfg["audit"]
    threshold = a.get("bad_site_threshold", 60)
    include_no_site = cfg["prospecting"].get("include_no_website", True)
    stats = {"qualified": 0, "disqualified": 0}
    for lead in db.leads("new"):
        result = auditor(lead.get("website"), timeout=a.get("timeout_seconds", 15),
                         use_pagespeed=a.get("use_pagespeed", False))
        has_site = result.reachable
        qualified = result.score < threshold and (lead.get("website") or include_no_site)
        status = "qualified" if qualified else "disqualified"
        db.update_lead(
            lead["id"],
            score=result.score,
            issues=[i.__dict__ for i in result.issues],
            email=lead.get("email") or (result.emails[0] if result.emails else None),
            recommended_tier=recommend_tier(cfg, lead.get("category"), result.score, has_site),
            status=status,
        )
        stats[status] += 1
        log.info("audit %s → %s/100 (%s)", lead["name"], result.score, status)
    return stats


def step_mockups(db: DB, cfg: dict[str, Any]) -> int:
    n = 0
    for lead in db.leads("qualified"):
        if lead.get("mockup_path"):
            continue
        path, _ = write_mockup(lead, cfg)
        db.update_lead(lead["id"], mockup_path=path)
        n += 1
    return n


def _mockup_url(lead: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    base = (cfg["business"].get("mockup_base_url") or "").rstrip("/")
    if not base or not lead.get("mockup_path"):
        return None
    return f"{base}/{Path(lead['mockup_path']).parent.name}/"


def _initial_subject(db: DB, lead_id: int) -> str:
    row = db.conn.execute(
        "SELECT subject FROM messages WHERE lead_id=? AND kind='initial' ORDER BY id LIMIT 1", (lead_id,)
    ).fetchone()
    return row["subject"] if row else ""


def step_followups(db: DB, cfg: dict[str, Any], mailer: Mailer, budget: int,
                   now: datetime | None = None) -> dict[str, int]:
    days = cfg["outreach"].get("followup_days", [3, 7, 14])
    stats = {"followups": 0, "lost": 0}
    for lead in db.leads("contacted"):
        due = next_followup(lead, days, now)
        if due == "lost":
            db.update_lead(lead["id"], status="lost")
            stats["lost"] += 1
        elif due and budget > 0:
            if db.is_opted_out(lead["email"]):
                db.update_lead(lead["id"], status="unsubscribed")
                continue
            subject, body = render_email(due, lead, cfg, mockup_url=_mockup_url(lead, cfg),
                                         original_subject=_initial_subject(db, lead["id"]))
            sent = mailer.send(lead["id"], due, lead["email"], subject, body)
            db.log_message(lead["id"], due, "email", subject, body, sent)
            db.update_lead(lead["id"], followups_sent=lead["followups_sent"] + 1, last_contact_at=now_iso())
            stats["followups"] += 1
            budget -= 1
    return stats


def step_outreach(db: DB, cfg: dict[str, Any], mailer: Mailer, budget: int) -> dict[str, int]:
    stats = {"emails": 0, "whatsapp": 0}
    for lead in db.leads("qualified"):
        url = _mockup_url(lead, cfg)
        if not lead.get("email"):
            text = render_whatsapp(lead, cfg, url)
            db.log_message(lead["id"], "initial", "whatsapp", "", text, False)
            db.update_lead(lead["id"], status="no_contact")
            stats["whatsapp"] += 1
            continue
        if db.is_opted_out(lead["email"]):
            db.update_lead(lead["id"], status="unsubscribed")
            continue
        if budget <= 0:
            continue
        subject, body = render_email("initial", lead, cfg, mockup_url=url)
        sent = mailer.send(lead["id"], "initial", lead["email"], subject, body)
        db.log_message(lead["id"], "initial", "email", subject, body, sent)
        db.update_lead(lead["id"], status="contacted", last_contact_at=now_iso())
        stats["emails"] += 1
        budget -= 1
    return stats


def step_replies(db: DB, cfg: dict[str, Any], replies: list[tuple[str, str]] | None = None) -> dict[str, int]:
    replies = fetch_replies(cfg) if replies is None else replies
    by_email = {l["email"].lower(): l for l in db.leads("contacted") if l.get("email")}
    stats = {"replied": 0, "unsubscribed": 0}
    for sender, text in replies:
        lead = by_email.get(sender)
        if not lead:
            continue
        if is_optout_reply(text):
            db.add_optout(sender)
            stats["unsubscribed"] += 1
        else:
            db.update_lead(lead["id"], status="replied")
            stats["replied"] += 1
    return stats


def write_report(db: DB, cfg: dict[str, Any], run_stats: dict[str, Any]) -> str:
    counts = db.counts()
    currency = cfg["business"].get("currency", "MAD")
    tiers = cfg["pricing"]["tiers"]
    pipeline_value = sum(
        tiers[l["recommended_tier"]]["price"]
        for l in db.leads(("qualified", "contacted", "replied", "no_contact")) if l.get("recommended_tier") in tiers
    )
    lines = [
        f"# Rapport YourWebsiteInADay — {date.today().isoformat()}",
        "",
        f"Mode d'envoi : **{cfg['outreach'].get('mode', 'draft')}**",
        "",
        "## Cette exécution",
        *(f"- {k} : {v}" for k, v in run_stats.items()),
        "",
        "## Pipeline",
        *(f"- {k} : {v}" for k, v in sorted(counts.items())),
        f"- Valeur potentielle (offres conseillées, leads actifs) : **{format_price(pipeline_value, currency)}**",
        "",
        "## À traiter en priorité (réponses reçues)",
    ]
    replied = db.leads("replied")
    lines += [f"- #{l['id']} {l['name']} — {l['email']} — offre {l['recommended_tier']}" for l in replied] or ["- aucune"]
    lines += ["", "## À contacter par WhatsApp / téléphone (pas d'email trouvé)"]
    wa = db.conn.execute(
        """SELECT l.id, l.name, l.phone, m.body FROM leads l JOIN messages m ON m.lead_id=l.id
           WHERE l.status='no_contact' AND m.channel='whatsapp' ORDER BY l.id"""
    ).fetchall()
    lines += [f"- #{r['id']} {r['name']} — {r['phone'] or 'pas de téléphone'}" for r in wa] or ["- aucun"]

    out = Path(cfg["paths"]["reports"])
    out.mkdir(parents=True, exist_ok=True)
    report = out / f"{date.today().isoformat()}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with open(out / "whatsapp_a_envoyer.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "nom", "telephone", "message"])
        for r in wa:
            w.writerow([r["id"], r["name"], r["phone"], r["body"]])
    return str(report)


def run_all(cfg: dict[str, Any], *, skip_discover: bool = False) -> tuple[dict[str, Any], str]:
    db = DB(cfg["paths"]["database"])
    mailer = Mailer(cfg)
    stats: dict[str, Any] = {}
    if not skip_discover:
        try:
            stats["nouveaux leads"] = step_discover(db, cfg)
        except Exception as exc:  # une source indisponible ne doit pas bloquer les relances
            log.warning("découverte impossible : %s", exc)
            stats["nouveaux leads"] = f"erreur ({exc.__class__.__name__})"
    stats.update(step_audit(db, cfg))
    stats["maquettes"] = step_mockups(db, cfg)
    stats.update(step_replies(db, cfg))
    budget = max(0, cfg["outreach"].get("daily_send_limit", 20) - db.sent_today())
    fu = step_followups(db, cfg, mailer, budget)
    stats.update(fu)
    stats.update(step_outreach(db, cfg, mailer, budget - fu["followups"]))
    stats["horodatage"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return stats, write_report(db, cfg, stats)
