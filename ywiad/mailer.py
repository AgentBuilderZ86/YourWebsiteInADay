"""Envoi SMTP direct et lecture des réponses (IMAP) — alternative au mode file d'attente + Gmail."""

from __future__ import annotations

import email
import email.policy
import imaplib
import os
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid, parseaddr
from typing import Any


def build_message(cfg: dict[str, Any], to: str, subject: str, body: str) -> EmailMessage:
    b = cfg["business"]
    msg = EmailMessage()
    msg["From"] = f"{b['sender_name']} <{b['sender_email']}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=b["sender_email"].split("@")[-1])
    msg["List-Unsubscribe"] = f"<mailto:{b['sender_email']}?subject=STOP>"
    msg.set_content(body)
    return msg


class Mailer:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.mode = cfg["outreach"].get("mode", "queue")

    def send(self, lead_id: int, kind: str, to: str, subject: str, body: str) -> None:
        """Envoi SMTP direct (mode smtp). En mode queue, c'est Claude qui envoie via Gmail."""
        s = self.cfg["outreach"]["smtp"]
        password = os.environ.get("SMTP_PASSWORD")
        if not password:
            raise RuntimeError("SMTP_PASSWORD manquant : impossible d'envoyer en mode smtp")
        with smtplib.SMTP(s["host"], s.get("port", 587), timeout=30) as smtp:
            smtp.starttls()
            smtp.login(s["username"], password)
            smtp.send_message(build_message(self.cfg, to, subject, body))


def fetch_replies(cfg: dict[str, Any], since_days: int = 30) -> list[tuple[str, str]]:
    """Retourne [(email_expéditeur, texte)] des emails reçus récemment."""
    i = cfg["outreach"].get("imap", {})
    password = os.environ.get("IMAP_PASSWORD")
    if not i.get("enabled") or not password:
        return []
    since = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
    out: list[tuple[str, str]] = []
    with imaplib.IMAP4_SSL(i["host"], i.get("port", 993)) as imap:
        imap.login(i["username"], password)
        imap.select("INBOX", readonly=True)
        _, data = imap.search(None, f'(SINCE "{since}")')
        for num in data[0].split():
            _, parts = imap.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(parts[0][1], policy=email.policy.default)
            sender = parseaddr(msg.get("From", ""))[1].lower()
            body = msg.get_body(preferencelist=("plain",))
            text = body.get_content() if body else ""
            out.append((sender, f"{msg.get('Subject', '')}\n{text}"))
    return out
