"""Orchestration : découverte → audit → maquette → file d'envoi → relances → rapport."""

from __future__ import annotations

import re

import csv
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .audit import find_own_site, FREE_MAIL, audit_url, best_emails, issue_dict
from .db import DB, now_iso
from .discover import discover, import_csv, next_combos, social_platform
from .mailer import Mailer, fetch_replies
from .mockup import mockup_slug, preview_path, render_previews, write_landing, write_mockup
from .outreach import is_optout_reply, next_followup, render_email, render_email_html, render_whatsapp
from .pricing import can_email_market, format_price, in_send_window, market, recommend_tier

log = logging.getLogger("ywiad")

# Marchés où un commerce sans email reste exploitable (message WhatsApp préparé)
WHATSAPP_MARKETS = ("MA",)


def keep_lead(lead: dict[str, Any]) -> bool:
    """Un lead sans email ni site est injoignable (sauf WhatsApp au Maroc)."""
    if lead.get("extra", {}).get("network"):
        return False  # agence d'un réseau / franchise : hors cible
    if lead.get("email") or lead.get("website"):
        return True
    return lead.get("market") in WHATSAPP_MARKETS and bool(lead.get("phone"))


def ingest(db: DB, leads: Iterable[dict[str, Any]], limit: int) -> int:
    created = 0
    for lead in leads:
        if not keep_lead(lead):
            continue
        _, is_new = db.upsert_lead(lead)
        created += is_new
        if created >= limit:
            break
    return created


def step_discover(db: DB, cfg: dict[str, Any]) -> tuple[int, list[str]]:
    p = cfg["prospecting"]
    combos, cursor = next_combos(cfg, db.get_state("cursor", 0), p.get("combos_per_run", 6))
    created = ingest(db, discover(cfg, combos), p.get("max_leads_per_run", 80))
    db.set_state("cursor", cursor)
    return created, [f"{cat}@{city}({code})" for code, city, cat in combos]


def step_import(db: DB, path: str) -> int:
    return ingest(db, import_csv(path), 10**9)


def website_from_email(lead: dict[str, Any]) -> str | None:
    """Commerce « sans site » mais email sur un domaine propre : ce domaine porte souvent un site."""
    if lead.get("website") or not lead.get("email") or "@" not in lead["email"]:
        return None
    domain = lead["email"].split("@", 1)[1].lower()
    return None if domain in FREE_MAIL else f"https://{domain}"


def priority(cfg: dict[str, Any], lead: dict[str, Any], score: int, issues: list[Any] | None = None) -> float:
    """Plus c'est haut, plus le lead est prometteur : site mauvais × valeur du métier × joignabilité."""
    cat = cfg["prospecting"]["categories"].get(lead.get("category") or "", {})
    p = (100 - score) * cat.get("value", 1.0)
    codes = {getattr(i, "code", None) or (i.get("code") if isinstance(i, dict) else None) for i in issues or []}
    if lead.get("website"):
        p *= 1.2   # un commerçant qui a déjà payé un site est plus facile à convaincre
        if {"stale", "not_mobile"} <= codes:
            p *= 1.2   # site ancien ET illisible sur téléphone : refonte évidente
    elif social_platform(lead.get("extra")):
        p *= 1.15  # déjà actif en ligne (Facebook, TheFork, Planity…) : sensible au sujet
    if lead.get("extra", {}).get("opening_hours"):
        p *= 1.05  # fiche entretenue : commerce actif
    return round(p, 1)


def step_audit(db: DB, cfg: dict[str, Any], auditor: Callable[..., Any] = audit_url, workers: int = 8) -> dict[str, int]:
    a = cfg["audit"]
    threshold = a.get("bad_site_threshold", 60)
    include_no_site = cfg["prospecting"].get("include_no_website", True)
    leads = db.leads("new")
    held = []
    for lead in leads:
        guessed = website_from_email(lead)
        if not guessed and not lead.get("website"):
            # Jamais « aucun site » sans avoir cherché un domaine à son nom
            guessed, state = find_own_site(lead["name"], lead.get("email"), lead.get("city"))
            if state == "construction":
                extra = {**(lead.get("extra") or {}), "audit_note": f"site en construction sur {guessed}"}
                db.update_lead(lead["id"], status="disqualified", extra=extra)
                held.append(lead["id"])
                continue
        if guessed:
            lead["website"] = guessed
            db.update_lead(lead["id"], website=guessed)
    leads = [l for l in leads if l["id"] not in held]
    kwargs = {"timeout": a.get("timeout_seconds", 15), "use_pagespeed": a.get("use_pagespeed", False)}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda l: auditor(l.get("website"), **kwargs), leads))
    stats = {"qualified": 0, "disqualified": 0, "no_contact": 0}
    if not leads:
        return stats
    for lead, result in zip(leads, results):
        # L'email public (OSM) est vérifié comme ceux trouvés sur le site
        candidates = best_emails(([lead["email"]] if lead.get("email") else []) + result.emails, lead.get("website"))
        email = candidates[0] if candidates else None
        if not result.verified or result.score >= threshold or (not lead.get("website") and not include_no_site):
            status = "disqualified"
        elif email or lead.get("market") in WHATSAPP_MARKETS:
            status = "qualified"
        else:
            status = "no_contact"  # site mauvais mais aucun moyen de contact écrit
        db.update_lead(
            lead["id"],
            score=result.score,
            issues=[issue_dict(i) for i in result.issues],
            email=email,
            priority=priority(cfg, lead, result.score, result.issues),
            extra=_extra_with_content(lead, result),
            recommended_tier=recommend_tier(cfg, lead.get("category"), result.score, result.reachable),
            status=status,
        )
        stats[status] += 1
        log.info("audit %s → %s/100 (%s)%s", lead["name"], result.score, status, f" [{result.note}]" if result.note else "")
    return stats


def _extra_with_content(lead: dict[str, Any], result: Any) -> dict[str, Any]:
    """Conserve le contenu réel du site (textes, photos, logo) pour une maquette de refonte personnalisée."""
    extra = {k: v for k, v in (lead.get("extra") or {}).items() if k not in ("site", "audit_note")}
    if result.note:
        extra["audit_note"] = result.note
    content = getattr(result, "content", None) or {}
    if result.verified and result.reachable and (content.get("headline") or content.get("photos") or content.get("sections")):
        extra["site"] = content
    return extra


def reaudit(db: DB, cfg: dict[str, Any], statuses: tuple[str, ...] = ("qualified", "contacted"),
            auditor: Callable[..., Any] = audit_url) -> list[tuple[str, int | None, int]]:
    """Ré-audite des leads actifs (site revendu, réparé…). Retourne [(nom, ancien score, nouveau score)]."""
    changes = []
    for lead in db.leads(statuses):
        if not lead.get("website"):
            continue
        result = auditor(lead["website"], timeout=cfg["audit"].get("timeout_seconds", 15))
        if not result.verified:
            continue
        db.update_lead(lead["id"], score=result.score, issues=[issue_dict(i) for i in result.issues],
                       extra=_extra_with_content(lead, result),
                       recommended_tier=recommend_tier(cfg, lead.get("category"), result.score, result.reachable))
        changes.append((lead["name"], lead.get("score"), result.score))
    return changes


def step_mockups(db: DB, cfg: dict[str, Any]) -> int:
    """(Re)génère les maquettes de tous les leads actifs + la page d'accueil. Retourne le nombre de nouvelles maquettes."""
    new, paths = 0, []
    for lead in db.leads(("qualified", "contacted", "replied", "won", "no_contact", "blocked")):
        if lead["status"] == "no_contact" and lead.get("market") not in WHATSAPP_MARKETS:
            continue
        path, _ = write_mockup(lead, cfg)
        paths.append(path)
        if not lead.get("mockup_path"):
            db.update_lead(lead["id"], mockup_path=path)
            new += 1
    render_previews(paths)
    write_landing(cfg)
    return new


def mockup_url(lead: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    base = (cfg["business"].get("mockup_base_url") or "").rstrip("/")
    if not base or not lead.get("mockup_path"):
        return None
    return f"{base}/{mockup_slug(lead)}/"


def _initial_subject(db: DB, lead_id: int) -> str:
    row = db.conn.execute(
        "SELECT subject FROM messages WHERE lead_id=? AND kind='initial' AND status='sent' ORDER BY id LIMIT 1",
        (lead_id,),
    ).fetchone()
    return row["subject"] if row else ""


def preview_url(lead: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    """URL de la capture de la maquette, seulement si elle a bien été générée (sinon email sans image)."""
    url = mockup_url(lead, cfg)
    return f"{url}preview.jpg" if url and preview_path(lead, cfg).exists() else None


def _dispatch(db: DB, cfg: dict[str, Any], mailer: Mailer, lead: dict[str, Any], kind: str,
              original_subject: str = "") -> None:
    """Envoi direct (smtp) ou mise en file (queue) pour envoi par Claude via Gmail."""
    url = mockup_url(lead, cfg)
    subject, body = render_email(kind, lead, cfg, mockup_url=url, original_subject=original_subject)
    html = render_email_html(kind, lead, cfg, mockup_url=url, preview_url=preview_url(lead, cfg),
                             original_subject=original_subject)
    if mailer.mode == "smtp":
        mailer.send(lead["id"], kind, lead["email"], subject, body, html)
        msg_id = db.log_message(lead["id"], kind, "email", subject, body, "sent", lead["email"], html)
        confirm(db, msg_id)
    else:
        db.log_message(lead["id"], kind, "email", subject, body, "queued", lead["email"], html)


def _sendable(db: DB, cfg: dict[str, Any], lead: dict[str, Any], now: datetime | None) -> bool:
    ok, why = can_email_market(cfg, lead.get("market"))
    if not ok:
        if lead["status"] == "qualified":
            db.update_lead(lead["id"], status="blocked")
            log.info("lead %s bloqué : %s", lead["name"], why)
        return False
    if db.is_opted_out(lead["email"]):
        db.update_lead(lead["id"], status="unsubscribed")
        return False
    return in_send_window(cfg, lead.get("market"), now) and not db.has_pending(lead["id"])


def step_followups(db: DB, cfg: dict[str, Any], mailer: Mailer, budget: int,
                   now: datetime | None = None) -> dict[str, int]:
    days = cfg["outreach"].get("followup_days", [3, 7, 14])
    stats = {"followups": 0, "lost": 0}
    for lead in db.leads("contacted"):
        due = next_followup(lead, days, now)
        if due == "lost":
            db.update_lead(lead["id"], status="lost")
            stats["lost"] += 1
        elif due and budget > 0 and _sendable(db, cfg, lead, now):
            _dispatch(db, cfg, mailer, lead, due, original_subject=_initial_subject(db, lead["id"]))
            stats["followups"] += 1
            budget -= 1
    return stats


def step_outreach(db: DB, cfg: dict[str, Any], mailer: Mailer, budget: int,
                  now: datetime | None = None) -> dict[str, int]:
    stats = {"emails": 0, "whatsapp": 0}
    for lead in db.leads("qualified"):  # triés par priorité décroissante
        if not lead.get("email"):
            db.log_message(lead["id"], "initial", "whatsapp", "", render_whatsapp(lead, cfg, mockup_url(lead, cfg)), "draft")
            db.update_lead(lead["id"], status="no_contact")
            stats["whatsapp"] += 1
            continue
        if budget <= 0 or not _sendable(db, cfg, lead, now):
            continue
        _dispatch(db, cfg, mailer, lead, "initial")
        stats["emails"] += 1
        budget -= 1
    return stats


# --- retours de l'envoi (appelés par Claude après chaque envoi Gmail) -----

def confirm(db: DB, msg_id: int, thread_id: str | None = None) -> dict[str, Any]:
    msg = db.message(msg_id)
    if not msg:
        raise ValueError(f"Message #{msg_id} introuvable")
    if msg["status"] == "sent":
        return msg  # idempotent : une double confirmation ne compte pas deux relances
    db.set_message_status(msg_id, "sent", thread_id=thread_id)
    lead = db.get_lead(msg["lead_id"])
    if msg["kind"] == "initial":
        db.update_lead(lead["id"], status="contacted", last_contact_at=now_iso())
    else:
        db.update_lead(lead["id"], followups_sent=lead["followups_sent"] + 1, last_contact_at=now_iso())
    return msg


def fail(db: DB, msg_id: int, reason: str, bounce: bool = False) -> None:
    msg = db.message(msg_id)
    if not msg:
        raise ValueError(f"Message #{msg_id} introuvable")
    db.set_message_status(msg_id, "failed", reason)
    if bounce:
        db.update_lead(msg["lead_id"], status="lost")


def expire_stale_queue(db: DB) -> int:
    """Les messages restés en file d'un jour sur l'autre sont régénérés (fenêtre horaire, budget)."""
    today = datetime.now(timezone.utc).date().isoformat()
    cur = db.conn.execute(
        "UPDATE messages SET status='failed', error='expiré (non envoyé)' WHERE status='queued' AND substr(created_at,1,10)<?",
        (today,),
    )
    db.conn.commit()
    return cur.rowcount


def inbound(db: DB, sender: str, text: str) -> str:
    """Traite une réponse reçue. Retourne le nouveau statut du lead (ou "inconnu")."""
    sender = sender.lower().strip()
    lead = next((l for l in db.leads(("contacted", "replied")) if (l.get("email") or "").lower() == sender), None)
    if not lead:
        return "inconnu"
    if is_optout_reply(text):
        db.add_optout(sender)
        return "unsubscribed"
    db.update_lead(lead["id"], status="replied")
    return "replied"


def step_replies(db: DB, cfg: dict[str, Any], replies: list[tuple[str, str]] | None = None) -> dict[str, int]:
    replies = fetch_replies(cfg) if replies is None else replies
    stats = {"replied": 0, "unsubscribed": 0}
    for sender, text in replies:
        status = inbound(db, sender, text)
        if status in stats:
            stats[status] += 1
    return stats


# --- rapport ---------------------------------------------------------------

def write_report(db: DB, cfg: dict[str, Any], run_stats: dict[str, Any]) -> str:
    rows = db.conn.execute(
        "SELECT market, status, COUNT(*) AS n FROM leads GROUP BY market, status ORDER BY market, status"
    ).fetchall()
    active = ("qualified", "contacted", "replied")
    by_market: dict[str, dict[str, int]] = {}
    for r in rows:
        by_market.setdefault(r["market"] or "?", {})[r["status"]] = r["n"]
    lines = [
        f"# Rapport YourWebsiteInADay — {date.today().isoformat()}",
        "",
        f"Mode d'envoi : **{cfg['outreach'].get('mode', 'queue')}** · "
        f"emails du jour : {db.emails_today()}/{cfg['outreach'].get('daily_send_limit', 25)}",
        "",
        "## Cette exécution",
        *(f"- {k} : {v}" for k, v in run_stats.items()),
        "",
        "## Pipeline par marché",
    ]
    for code, counts in sorted(by_market.items()):
        m = market(cfg, code)
        value = sum(
            m["prices"][l["recommended_tier"]] for l in db.leads(active)
            if l.get("market") == code and l.get("recommended_tier") in m.get("prices", {})
        )
        detail = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        lines.append(f"- **{code}** — {detail} — valeur active : {format_price(value, m)}")
    lines += ["", "## Réponses à traiter"]
    lines += [f"- #{l['id']} {l['name']} ({l['market']}) — {l['email']} — offre {l['recommended_tier']}"
              for l in db.leads("replied")] or ["- aucune"]
    blocked = db.leads("blocked")
    if blocked:
        lines += ["", f"## Bloqués ({len(blocked)}) — adresse postale requise (US/CA) : renseigner business.postal_address"]
    wa = db.conn.execute(
        """SELECT l.id, l.name, l.phone, m.body FROM leads l JOIN messages m ON m.lead_id=l.id
           WHERE l.status='no_contact' AND m.channel='whatsapp' ORDER BY l.id"""
    ).fetchall()
    lines += ["", f"## WhatsApp à envoyer (Maroc, pas d'email) : {len(wa)} — voir whatsapp_a_envoyer.csv"]

    out = Path(cfg["paths"]["reports"])
    out.mkdir(parents=True, exist_ok=True)
    report = out / f"{date.today().isoformat()}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "whatsapp.html").write_text(whatsapp_page(db, cfg), encoding="utf-8")
    with open(out / "whatsapp_a_envoyer.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "nom", "telephone", "message"])
        for r in wa:
            w.writerow([r["id"], r["name"], r["phone"], r["body"]])
    return str(report)


def run_all(cfg: dict[str, Any], *, skip_discover: bool = False, now: datetime | None = None) -> tuple[dict[str, Any], str]:
    db = DB(cfg["paths"]["database"])
    mailer = Mailer(cfg)
    stats: dict[str, Any] = {"file expirée": expire_stale_queue(db)}
    for lead in db.leads("blocked"):  # ex. adresse postale ajoutée depuis
        if can_email_market(cfg, lead.get("market"))[0]:
            db.update_lead(lead["id"], status="qualified")
    if not skip_discover:
        try:
            stats["nouveaux leads"], stats["cibles"] = step_discover(db, cfg)
        except Exception as exc:  # une source indisponible ne doit pas bloquer les relances
            log.warning("découverte impossible : %s", exc)
            stats["nouveaux leads"] = f"erreur ({exc})"
    stats.update(step_audit(db, cfg))
    stats["maquettes"] = step_mockups(db, cfg)
    stats.update(step_replies(db, cfg))
    budget = max(0, cfg["outreach"].get("daily_send_limit", 25) - db.emails_today())
    fu = step_followups(db, cfg, mailer, budget, now)
    stats.update(fu)
    stats.update(step_outreach(db, cfg, mailer, budget - fu["followups"], now))
    stats["en file"] = len(db.messages("queued"))
    return stats, write_report(db, cfg, stats)


def _intl_phone(phone: str | None, prefix: str) -> str | None:
    d = re.sub(r"\D", "", (phone or "").split(";")[0])
    if d.startswith("00"):
        d = d[2:]
    elif d.startswith("0"):
        d = prefix + d[1:]
    return d if d.startswith(prefix) and len(d) >= 11 else None


def mark_whatsapp_sent(db: DB, lead_id: int) -> str:
    """AZ a envoyé le WhatsApp préparé : on le date (suivi, relance manuelle à J+3 dans le rapport)."""
    lead = db.get_lead(lead_id)
    if not lead:
        return f"#{lead_id} introuvable"
    extra = {**(lead.get("extra") or {}), "wa_sent_at": date.today().isoformat()}
    db.update_lead(lead_id, extra=extra)
    db.conn.execute("UPDATE messages SET status='sent', sent_at=? WHERE lead_id=? AND channel='whatsapp' AND status='draft'",
                    (date.today().isoformat(), lead_id))
    db.conn.commit()
    return f"#{lead_id} {lead['name']} : WhatsApp envoyé le {extra['wa_sent_at']}"


def whatsapp_page(db: DB, cfg: dict[str, Any]) -> str:
    """Page à ouvrir sur téléphone : un bouton WhatsApp pré-rempli par commerce marocain sans email
    (mobiles d'abord, par priorité), et un bouton d'appel pour les fixes. Coché = mémorisé sur l'appareil."""
    from html import escape as esc
    from urllib.parse import quote
    rows = []
    for lead in db.leads("no_contact"):
        if lead.get("market") not in WHATSAPP_MARKETS or db.is_opted_out(lead.get("email") or ""):
            continue
        num = _intl_phone(lead.get("phone"), "212")
        if not num:
            continue
        mobile = num[3] in "67"
        msg = render_whatsapp(lead, cfg, mockup_url(lead, cfg))
        rows.append((not mobile, -(lead.get("priority") or 0), lead, num, mobile, msg))
    rows.sort(key=lambda r: (r[0], r[1]))
    items = []
    for _, _, lead, num, mobile, msg in rows:
        cat = cfg["prospecting"]["categories"].get(lead.get("category") or "", {}).get("fr", "")
        action = (f'<a class="btn wa" href="https://wa.me/{num}?text={quote(msg)}">WhatsApp</a>' if mobile
                  else f'<a class="btn tel" href="tel:+{num}">Appeler (fixe)</a>')
        sent = (lead.get("extra") or {}).get("wa_sent_at")
        items.append(f'''<li id="l{lead["id"]}"{' class="done"' if sent else ''}><label><input type="checkbox" data-id="{lead["id"]}"{' checked data-sent="1"' if sent else ''}>
<b>{esc(lead["name"])}</b> <span>{esc(cat)} · {esc(lead.get("city") or "")} · +{num}</span></label>
<div class="row">{action}<a class="btn ghost" href="{esc(mockup_url(lead, cfg) or "")}">Maquette</a></div>
<details><summary>Message</summary><p>{esc(msg)}</p></details></li>''')
    return f'''<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>WhatsApp à envoyer</title>
<style>:root{{--bg:#f3efe9;--card:#fff;--ink:#1a1614;--muted:#6b625c;--wa:#1f8f4e;--line:#e8e2da}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161412;--card:#221f1c;--ink:#f3efe9;--muted:#a79d95;--line:#3a3430}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 -apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:640px;margin:0 auto;padding:20px 16px 60px}} h1{{font-family:Georgia,serif;font-weight:400}}
ul{{list-style:none;padding:0}} li{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:0 0 10px}}
li.done{{opacity:.45}} span{{color:var(--muted);font-size:13px;display:block}} .row{{display:flex;gap:8px;margin-top:10px}}
.btn{{flex:1;text-align:center;padding:11px;border-radius:999px;text-decoration:none;font-weight:600}}
.wa{{background:var(--wa);color:#fff}} .tel{{background:var(--ink);color:var(--bg)}} .ghost{{border:1px solid var(--line);color:var(--ink)}}
details{{margin-top:8px;color:var(--muted);font-size:13px}} input{{margin-right:8px;transform:scale(1.2)}}</style></head>
<body><main><h1>WhatsApp à envoyer</h1><p>{sum(1 for r in rows if r[4])} mobiles (WhatsApp) et
{sum(1 for r in rows if not r[4])} fixes (appel), triés par priorité. Coche chaque commerce une fois contacté.
Une réponse « OUI » : transfère-la-moi ou note-la, je prends la suite.</p><p><a class="btn wa" id="send" href="#" style="display:block">Envoyer la liste des cochés à Claude</a></p>
<ul>{"".join(items)}</ul></main>
<script>const k="ywiad-wa-done";let d={{}};try{{d=JSON.parse(localStorage.getItem(k)||"{{}}")}}catch(e){{}}
document.querySelectorAll("input[data-id]").forEach(c=>{{const li=c.closest("li");c.checked=!!d[c.dataset.id]||!!c.dataset.sent;li.classList.toggle("done",c.checked);
c.addEventListener("change",()=>{{d[c.dataset.id]=c.checked;li.classList.toggle("done",c.checked);try{{localStorage.setItem(k,JSON.stringify(d))}}catch(e){{}}}})}});
document.getElementById("send").addEventListener("click",e=>{{const ids=[...document.querySelectorAll("input[data-id]:checked")].map(c=>c.dataset.id);
e.currentTarget.href="mailto:{cfg["business"]["sender_email"]}?subject="+encodeURIComponent("[YWIAD-WA] envoyés")+"&body="+encodeURIComponent("ids: "+ids.join(","));}});</script>
</body></html>'''
