"""Chargement de la configuration YAML."""

from __future__ import annotations

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
            return cfg
    raise FileNotFoundError(f"Aucun fichier de configuration trouvé parmi : {candidates}")
