"""Load non-secret model metadata from static configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_model_catalog(path: Path) -> dict[str, Any]:
    """Load and validate the top-level catalog shape."""

    resolved_path = path
    if not path.is_absolute() and not path.exists():
        source_root_candidate = Path(__file__).resolve().parents[3] / path
        if source_root_candidate.exists():
            resolved_path = source_root_candidate
    try:
        raw = yaml.safe_load(resolved_path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ValueError(f"Model catalog could not be read: {path}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("vendors"), dict):
        raise ValueError("Model catalog must contain a 'vendors' mapping.")
    return raw["vendors"]


__all__ = ["load_model_catalog"]
