"""Helpers for loading secrets across local and hosted environments."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

try:
    import streamlit as st
except ImportError:  # pragma: no cover - streamlit is installed in app environments
    st = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_local_secrets(project_root: Path | None = None) -> dict[str, str]:
    secrets_path = (project_root or PROJECT_ROOT) / "secrets.txt"
    secrets: dict[str, str] = {}
    if not secrets_path.exists():
        return secrets

    for line in secrets_path.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip()
        if "=" in cleaned and not cleaned.startswith("#"):
            key, value = cleaned.split("=", 1)
            secrets[key.strip()] = value.strip()
    return secrets


def _read_streamlit_secrets() -> dict[str, str]:
    if st is None:
        return {}

    try:
        return {key: str(value).strip() for key, value in dict(st.secrets).items()}
    except Exception:
        return {}


def get_secret(
    name: str,
    *,
    project_root: Path | None = None,
    streamlit_secrets: Mapping[str, Any] | None = None,
) -> str | None:
    """Resolve a secret from env vars, Streamlit secrets, then local secrets.txt."""
    env_value = os.environ.get(name, "").strip()
    if env_value:
        return env_value

    secrets_mapping = (
        {key: str(value).strip() for key, value in streamlit_secrets.items()}
        if streamlit_secrets is not None
        else _read_streamlit_secrets()
    )
    streamlit_value = secrets_mapping.get(name, "").strip()
    if streamlit_value:
        return streamlit_value

    local_value = _read_local_secrets(project_root=project_root).get(name, "").strip()
    return local_value or None
