"""Mini-CRM SQLite : leads, audits et historique des messages."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Cycle de vie d'un lead
STATUSES = (
    "new",           # découvert, pas encore audité
    "disqualified",  # site correct : on ne prospecte pas
    "qualified",     # site mauvais ou absent : à contacter
    "contacted",     # premier email envoyé
    "replied",       # le commerçant a répondu -> reprise humaine / Claude
    "won",           # devis signé
    "lost",          # séquence de relances épuisée ou refus
    "unsubscribed",  # a demandé à ne plus être contacté
    "no_contact",    # qualifié mais aucun email trouvé (WhatsApp / téléphone)
    "blocked",       # qualifié mais marché non envoyable (ex. adresse postale manquante)
)
MESSAGE_STATUSES = ("queued", "sent", "failed", "draft")

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT,
    market TEXT,
    website TEXT,
    email TEXT,
    phone TEXT,
    address TEXT,
    city TEXT,
    extra TEXT DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'new',
    score INTEGER,
    priority REAL,
    issues TEXT DEFAULT '[]',
    recommended_tier TEXT,
    mockup_path TEXT,
    followups_sent INTEGER NOT NULL DEFAULT 0,
    last_contact_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source, source_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL REFERENCES leads(id),
    kind TEXT NOT NULL,
    channel TEXT NOT NULL,
    subject TEXT,
    body TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    to_addr TEXT,
    error TEXT,
    thread_id TEXT,
    html TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS optouts (
    email TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # Migration douce des bases créées avant l'ajout de colonnes
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(messages)")}
        for col in ("thread_id", "html"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE messages ADD COLUMN {col} TEXT")

    # --- leads -----------------------------------------------------------
    def upsert_lead(self, lead: dict[str, Any]) -> tuple[int, bool]:
        """Insère un lead s'il est nouveau. Retourne (id, créé ?)."""
        row = self.conn.execute(
            "SELECT id FROM leads WHERE source=? AND source_id=?",
            (lead["source"], lead["source_id"]),
        ).fetchone()
        if row:
            return row["id"], False
        # Dédoublonnage inter-sources par site web
        if lead.get("website"):
            row = self.conn.execute(
                "SELECT id FROM leads WHERE website=?", (lead["website"],)
            ).fetchone()
            if row:
                return row["id"], False
        ts = now_iso()
        cur = self.conn.execute(
            """INSERT INTO leads (source, source_id, name, category, market, website, email, phone,
                                  address, city, extra, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                lead["source"], lead["source_id"], lead["name"], lead.get("category"), lead.get("market"),
                lead.get("website"), lead.get("email"), lead.get("phone"),
                lead.get("address"), lead.get("city"),
                json.dumps(lead.get("extra", {}), ensure_ascii=False), ts, ts,
            ),
        )
        self.conn.commit()
        return cur.lastrowid, True

    def update_lead(self, lead_id: int, **fields: Any) -> None:
        for key in ("issues", "extra"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key], ensure_ascii=False)
        if "status" in fields and fields["status"] not in STATUSES:
            raise ValueError(f"Statut inconnu : {fields['status']}")
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE leads SET {cols} WHERE id=?", (*fields.values(), lead_id))
        self.conn.commit()

    def get_lead(self, lead_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return _row(row) if row else None

    def leads(self, status: str | Iterable[str] | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM leads", []
        if status:
            statuses = [status] if isinstance(status, str) else list(status)
            sql += f" WHERE status IN ({','.join('?' * len(statuses))})"
            params.extend(statuses)
        sql += " ORDER BY COALESCE(priority, 0) DESC, id"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return [_row(r) for r in self.conn.execute(sql, params)]

    def counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM leads GROUP BY status")}

    # --- messages --------------------------------------------------------
    def log_message(self, lead_id: int, kind: str, channel: str, subject: str, body: str,
                    status: str, to_addr: str | None = None, html: str | None = None) -> int:
        if status not in MESSAGE_STATUSES:
            raise ValueError(f"Statut de message inconnu : {status}")
        ts = now_iso()
        cur = self.conn.execute(
            """INSERT INTO messages (lead_id, kind, channel, subject, body, status, to_addr, html, created_at, sent_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (lead_id, kind, channel, subject, body, status, to_addr, html, ts, ts if status == "sent" else None),
        )
        self.conn.commit()
        return cur.lastrowid

    def message(self, msg_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
        return dict(row) if row else None

    def messages(self, status: str, channel: str = "email") -> list[dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM messages WHERE status=? AND channel=? ORDER BY id", (status, channel))]

    def set_message_status(self, msg_id: int, status: str, error: str | None = None,
                           thread_id: str | None = None) -> None:
        self.conn.execute(
            "UPDATE messages SET status=?, error=?, sent_at=?, thread_id=COALESCE(?, thread_id) WHERE id=?",
            (status, error, now_iso() if status == "sent" else None, thread_id, msg_id),
        )
        self.conn.commit()

    def lead_thread(self, lead_id: int) -> str | None:
        """Fil Gmail du premier contact, pour y rattacher les relances."""
        row = self.conn.execute(
            "SELECT thread_id FROM messages WHERE lead_id=? AND status='sent' AND thread_id IS NOT NULL ORDER BY id LIMIT 1",
            (lead_id,),
        ).fetchone()
        return row["thread_id"] if row else None

    def has_pending(self, lead_id: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM messages WHERE lead_id=? AND status='queued'", (lead_id,)
        ).fetchone() is not None

    def emails_today(self) -> int:
        """Emails envoyés aujourd'hui + emails encore en file (ils partiront aujourd'hui)."""
        today = datetime.now(timezone.utc).date().isoformat()
        return self.conn.execute(
            """SELECT COUNT(*) FROM messages WHERE channel='email'
               AND (status='queued' OR (status='sent' AND substr(sent_at,1,10)=?))""", (today,)
        ).fetchone()[0]

    def emails_today_market(self, code: str) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        return self.conn.execute(
            """SELECT COUNT(*) FROM messages m JOIN leads l ON l.id = m.lead_id WHERE m.channel='email' AND l.market=?
               AND (m.status='queued' OR (m.status='sent' AND substr(m.sent_at,1,10)=?))""", (code, today)
        ).fetchone()[0]

    # --- état persistant (curseur de rotation, géocodage) -----------------
    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT OR REPLACE INTO state VALUES (?,?)", (key, json.dumps(value)))
        self.conn.commit()

    # --- opt-out ---------------------------------------------------------
    def add_optout(self, email: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO optouts VALUES (?,?)", (email.lower(), now_iso()))
        self.conn.execute(
            "UPDATE leads SET status='unsubscribed', updated_at=? WHERE lower(email)=?",
            (now_iso(), email.lower()),
        )
        self.conn.commit()

    def is_opted_out(self, email: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM optouts WHERE email=?", (email.lower(),)
        ).fetchone() is not None


def _row(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["issues"] = json.loads(d.get("issues") or "[]")
    d["extra"] = json.loads(d.get("extra") or "{}")
    return d
