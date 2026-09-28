"""Chargement de la configuration YAML."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_FILES = ("config.yaml", "config.example.yaml")


def load_config(path: str | None = None) -> dict[str, Any]:
    candidates = [path] if path else list(DEFAULT_CONFIG_FILES)
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            with open(candidate, encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
            cfg["_source"] = candidate
            _private_settings(cfg)
            return cfg
    raise FileNotFoundError(f"Aucun fichier de configuration trouvé parmi : {candidates}")


def _private_settings(cfg: dict[str, Any]) -> None:
    """Données privées hors du dépôt public : l'adresse postale (exigée aux US/CA) vient de la variable
    YWIAD_POSTAL_ADDRESS ou de la base CRM chiffrée (`ywiad set-address`), jamais du YAML versionné."""
    business = cfg.setdefault("business", {})
    if business.get("postal_address"):
        return
    address = os.environ.get("YWIAD_POSTAL_ADDRESS")
    db = (cfg.get("paths") or {}).get("database")
    if not address and db and Path(db).exists():
        try:
            with sqlite3.connect(db) as conn:
                row = conn.execute("SELECT value FROM state WHERE key='postal_address'").fetchone()
            address = json.loads(row[0]) if row else None
        except sqlite3.Error:
            address = None
    if address:
        business["postal_address"] = address
