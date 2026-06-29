"""Streamlit front end for the LeaseGuard AI project."""

from __future__ import annotations

import copy
import hmac
import html
import importlib.util
import json
from functools import lru_cache
from mimetypes import guess_type
import re
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

try:
    from frontend.audit_backend import (
        LeaseTextExtractionError,
        extract_lease_text,
        run_live_pipeline,
        run_reader_pipeline,
        run_sample_pipeline,
    )
    from frontend.secrets_utils import get_secret, prepare_network_env
except ImportError:
    from audit_backend import (
        LeaseTextExtractionError,
        extract_lease_text,
        run_live_pipeline,
        run_reader_pipeline,
        run_sample_pipeline,
    )
    from secrets_utils import get_secret, prepare_network_env


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHROMADB_PATH = PROJECT_ROOT / "agent" / "chromadb"

COMMON_TERMS = [
    "Joint and Several Liability",
    "Abatement",
    "Holdover Tenancy",
    "Security Deposit Interest",
    "Quiet Enjoyment",
    "Subletting vs. Assignment",
    "Constructive Eviction",
    "Lease Guaranty",
]

TERM_FALLBACKS: dict[str, str] = {
    "joint and several liability": """
## Definition
This clause means each tenant can be held responsible for the full rent and any damages, not just their own share. If one roommate stops paying, the landlord can pursue the others for the entire amount.

## How It Works in a Lease
It usually appears in shared housing leases where multiple tenants sign the same contract. Landlords use it to reduce collection risk.

## Financial Implications for Tenants
- You may have to cover a roommate's missed rent to avoid default.
- It can create conflict even when you personally paid on time.
- It increases the value of screening roommates carefully before signing.

## Negotiation Tip
Ask whether each tenant can sign a separate lease or whether liability can be limited to each renter's share.
""".strip(),
    "abatement": """
## Definition
Abatement is a reduction in rent when a serious housing problem makes part of the unit unusable. It is meant to reflect that the tenant is not receiving the full value of the home.

## How It Works in a Lease
It often comes up when repairs are delayed after issues like no heat, water damage, or major appliance failure. Some leases mention it directly, while others leave the process vague.

## Financial Implications for Tenants
- It may reduce how much rent you owe during a major problem.
- It can matter a lot if repairs drag on for weeks.
- Poorly written clauses can make it harder to request a fair adjustment.

## Negotiation Tip
Ask for a clear process that explains when rent relief applies and how it will be calculated.
""".strip(),
    "holdover tenancy": """
## Definition
Holdover tenancy happens when a renter stays after the lease term ends without signing a new agreement. That can trigger a month-to-month arrangement or penalties, depending on the lease and local law.

## How It Works in a Lease
Leases often describe what happens if the tenant does not move out on time. Some clauses raise the rent sharply during the holdover period.

## Financial Implications for Tenants
- Holdover rent is often much higher than normal monthly rent.
- It can create leverage against the tenant during move-out disputes.
- It increases the cost of timing mistakes around renewal or relocation.

## Negotiation Tip
Ask for a reasonable holdover rate and a short cure period for accidental overlap during move-out.
""".strip(),
}

RESOURCE_FALLBACKS: dict[str, list[dict[str, str | None]]] = {
    "chicago, il": [
        {
            "name": "Metropolitan Tenants Organization",
            "type": "Non-Profit",
            "description": (
                "Offers tenant counseling, education, and practical guidance for renters "
                "dealing with lease issues or housing instability."
            ),
            "url": "https://www.tenants-rights.org",
            "phone": "773-292-4988",
        },
        {
            "name": "Lawyers' Committee for Better Housing",
            "type": "Legal Aid",
            "description": (
                "Provides legal support and policy advocacy focused on housing justice, "
                "eviction prevention, and renter protections in Chicago."
            ),
            "url": "https://lcbh.org",
            "phone": "312-347-7600",
        },
        {
            "name": "Legal Aid Chicago",
            "type": "Legal Aid",
            "description": (
                "Connects qualifying residents with attorneys and legal resources across a "
                "range of civil matters, including housing."
            ),
            "url": "https://www.legalaidchicago.org",
            "phone": "312-341-1070",
        },
    ],
    "seattle, wa": [
        {
            "name": "Tenants Union of Washington State",
            "type": "Non-Profit",
            "description": (
                "Provides renter education, organizing support, and help understanding tenant "
                "rights across Washington."
            ),
            "url": "https://tenantsunion.org",
            "phone": None,
        },
        {
            "name": "Solid Ground Tenant Services",
            "type": "Non-Profit",
            "description": (
                "Offers support on landlord-tenant concerns, housing stability, and referrals "
                "for Seattle-area renters."
            ),
            "url": "https://www.solid-ground.org",
            "phone": None,
        },
    ],
}

NATIONAL_RESOURCES: list[dict[str, str | None]] = [
    {
        "name": "Legal Services Corporation",
        "type": "Legal Aid",
        "description": (
            "A national directory that helps people find local legal aid organizations by state "
            "and issue area."
        ),
        "url": "https://www.lsc.gov/about-lsc/what-legal-aid/get-legal-help",
        "phone": None,
    },
    {
        "name": "HUD Tenant Rights Resources",
        "type": "Government",
        "description": (
            "Provides federal housing guidance and links to state and local housing resources "
            "that can help renters navigate disputes."
        ),
        "url": "https://www.hud.gov/topics/rental_assistance",
        "phone": None,
    },
    {
        "name": "211",
        "type": "Hotline",
        "description": (
            "A broad community referral line that can point renters to local housing, legal, "
            "and financial assistance programs."
        ),
        "url": "https://www.211.org",
        "phone": "211",
    },
]

STATE_ABBREVIATIONS = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}

STATE_NAMES_TO_ABBR = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}

STATE_ABBR_TO_NAME = {abbr: name.title() for name, abbr in STATE_NAMES_TO_ABBR.items()}


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _get_secret_value(name: str) -> str | None:
    return get_secret(name, project_root=PROJECT_ROOT)


def _load_api_key() -> str | None:
    return _get_secret_value("OPENAI_API_KEY")


def _get_app_password() -> str | None:
    return _get_secret_value("LEASEGUARD_APP_PASSWORD")


def _can_use_live_llm() -> bool:
    api_key = _load_api_key()
    if not api_key:
        return False
    return _module_available("openai")


def _call_llm(system_prompt: str, user_prompt: str) -> str:
    from openai import OpenAI

    api_key = _load_api_key()
    if not api_key:
        raise RuntimeError("No OPENAI_API_KEY found in environment variables, app secrets, or local secrets.txt.")

    prepare_network_env()
    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=900,
    )
    return response.choices[0].message.content or ""


@lru_cache(maxsize=1)
def _live_audit_status() -> dict[str, Any]:
    live_llm_ready = _can_use_live_llm()
    requirements = {
        "LangChain OpenAI": _module_available("langchain_openai"),
        "LlamaIndex": _module_available("llama_index"),
        "ChromaDB": _module_available("chromadb"),
        "Chroma index": CHROMADB_PATH.exists() and any(CHROMADB_PATH.iterdir()),
        "PDF parser": _module_available("pypdf"),
    }
    missing = [name for name, available in requirements.items() if not available]
    if not live_llm_ready or missing:
        return {"available": False, "missing": missing, "error": "live_stack_unavailable"}

    try:
        from frontend.lease_agent import check_live_audit_ready
    except ImportError:
        from lease_agent import check_live_audit_ready

    ready, error = check_live_audit_ready()
    return {
        "available": ready,
        "missing": [] if ready else missing,
        "error": "" if ready else (error or "live_stack_unavailable"),
    }


def _normalize_review_error(exc: Exception) -> str:
    error_text = str(exc).strip()
    lowered = error_text.lower()
    if error_text in {"chroma_index_missing", "chroma_runtime_bootstrap_failed"}:
        return error_text
    if "readonly" in lowered:
        return "chroma_runtime_bootstrap_failed"
    if "connection error" in lowered or "apiconnectionerror" in lowered or "winerror 10061" in lowered:
        return "openai_connection_failed"
    return "live_review_failed"


def _get_capabilities() -> dict[str, Any]:
    live_status = _live_audit_status()
    return {
        "sample_review": {"available": True},
        "live_llm": {"available": _can_use_live_llm()},
        "full_live_audit": live_status,
    }


def _init_state() -> None:
    st.session_state.setdefault("app_authenticated", False)
    st.session_state.setdefault("app_auth_error", "")
    st.session_state.setdefault("active_mode", "translation")
    st.session_state.setdefault("upload_key_version", 0)
    st.session_state.setdefault("viewer_page", 0)
    st.session_state.setdefault("viewer_zoom", 1.0)
    st.session_state.setdefault("viewer_source_name", "")
    st.session_state.setdefault("workspace_context", "")
    st.session_state.setdefault("audit_results", None)
    st.session_state.setdefault("audit_source", None)
    st.session_state.setdefault("reader_results", None)
    st.session_state.setdefault("reader_source", None)
    st.session_state.setdefault("pending_term", None)
    st.session_state.setdefault("last_term", None)
    st.session_state.setdefault("last_result", None)


def _load_sample_audit() -> None:
    results = copy.deepcopy(run_sample_pipeline())
    st.session_state["audit_results"] = results
    st.session_state["audit_source"] = "sample"
    st.session_state["reader_results"] = results
    st.session_state["reader_source"] = "sample"
    st.session_state["active_mode"] = "translation"
    st.session_state["viewer_source_name"] = ""
    st.session_state["viewer_page"] = 0
    st.session_state["viewer_zoom"] = 1.0


def _mime_type_for_name(name: str) -> str | None:
    mime_type, _ = guess_type(name)
    return mime_type


def _source_name(results: dict[str, Any]) -> str:
    return str(results.get("source_name") or "").strip()


def _source_preview_bytes(results: dict[str, Any]) -> bytes | None:
    preview_bytes = results.get("source_bytes")
    if isinstance(preview_bytes, bytes):
        return preview_bytes
    return None


def _source_mime_type(results: dict[str, Any]) -> str | None:
    mime_type = results.get("source_mime_type")
    if isinstance(mime_type, str) and mime_type:
        return mime_type
    source_name = _source_name(results)
    return _mime_type_for_name(source_name) if source_name else None


def _attach_uploaded_source_metadata(
    results: dict[str, Any],
    source_name: str,
    source_bytes: bytes,
    source_mime_type: str | None,
) -> None:
    results["source_name"] = source_name
    results["source_bytes"] = source_bytes
    results["source_mime_type"] = source_mime_type or _mime_type_for_name(source_name)


def _current_results() -> tuple[dict[str, Any] | None, str | None]:
    if st.session_state.get("audit_results"):
        return st.session_state["audit_results"], st.session_state.get("audit_source")
    if st.session_state.get("reader_results"):
        return st.session_state["reader_results"], st.session_state.get("reader_source")
    return None, None


def _set_active_mode(mode: str) -> None:
    st.session_state["active_mode"] = mode


def _set_viewer_zoom(zoom_value: float) -> None:
    st.session_state["viewer_zoom"] = max(0.8, min(1.6, round(zoom_value, 2)))


def _clear_workspace(preserve_context: bool = True) -> None:
    st.session_state["audit_results"] = None
    st.session_state["audit_source"] = None
    st.session_state["reader_results"] = None
    st.session_state["reader_source"] = None
    st.session_state["viewer_page"] = 0
    st.session_state["viewer_zoom"] = 1.0
    st.session_state["viewer_source_name"] = ""
    st.session_state["active_mode"] = "translation"
    st.session_state["upload_key_version"] = st.session_state.get("upload_key_version", 0) + 1
    if not preserve_context:
        st.session_state["workspace_context"] = ""


def _compact_explanation(text: str, sentence_limit: int = 2) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "No explanation available."
    pieces = re.split(r"(?<=[.!?])\s+", cleaned)
    trimmed = " ".join(piece for piece in pieces[:sentence_limit] if piece)
    shortened = trimmed or cleaned
    if len(shortened) > 260:
        shortened = shortened[:257].rsplit(" ", 1)[0] + "..."
    return shortened


def _severity_level(score: float | int | None) -> str:
    if score is None:
        return "review"
    if score >= 7:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def _severity_markup(score: float | int | None) -> str:
    if score is None:
        return ":gray[Needs review]"
    if score >= 7:
        return ":red[High risk]"
    if score >= 4:
        return ":orange[Medium risk]"
    return ":green[Low risk]"


def _severity_badge_html(score: float | int | None) -> str:
    level = _severity_level(score)
    label = {
        "high": "High risk",
        "medium": "Medium risk",
        "low": "Low risk",
        "review": "Needs review",
    }[level]
    score_text = f"{score:g}/10" if score is not None else ""
    return (
        f"<div class='leaseguard-risk-badge leaseguard-risk-{level}'>"
        f"{html.escape(label)}"
        f"{f'<span>{html.escape(score_text)}</span>' if score_text else ''}"
        "</div>"
    )


def _icon_svg(name: str, color: str = "currentColor") -> str:
    icons = {
        "brand": (
            '<path d="M12 3.2 4.6 7v5.5c0 4.3 3 8.2 7.4 9.3 4.4-1.1 7.4-5 7.4-9.3V7L12 3.2Z" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M8.9 12.2 12 9.6l3.1 2.6v3.8H8.9z" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        ),
        "file": (
            '<path d="M8 3.8h5.8l4.2 4.2v11.8a1.6 1.6 0 0 1-1.6 1.6H8a1.6 1.6 0 0 1-1.6-1.6V5.4A1.6 1.6 0 0 1 8 3.8Z" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M13.8 3.8V8h4.2" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M9.4 12.2h5.2M9.4 15.4h5.2" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round"/>'
        ),
        "shield": (
            '<path d="M12 3.4 5.4 6.2v4.7c0 4.2 2.7 7.9 6.6 9 3.9-1.1 6.6-4.8 6.6-9V6.2L12 3.4Z" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="m9.4 12.1 1.7 1.8 3.5-3.9" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
        ),
        "pin": (
            '<path d="M12 20.2s5.9-4.5 5.9-9.7A5.9 5.9 0 1 0 6.1 10.5c0 5.2 5.9 9.7 5.9 9.7Z" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
            '<circle cx="12" cy="10.5" r="2.2" fill="none" stroke="{color}" stroke-width="1.7"/>'
        ),
        "users": (
            '<path d="M8.3 18.4v-1.2a3.2 3.2 0 0 1 3.2-3.2h1a3.2 3.2 0 0 1 3.2 3.2v1.2" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
            '<circle cx="12" cy="8.8" r="2.8" fill="none" stroke="{color}" stroke-width="1.7"/>'
            '<path d="M5.3 18.2v-.7a2.3 2.3 0 0 1 2.1-2.3M18.7 18.2v-.7a2.3 2.3 0 0 0-2.1-2.3" fill="none" stroke="{color}" stroke-width="1.5" stroke-linecap="round"/>'
        ),
        "home": (
            '<path d="m4.9 11.3 7.1-5.8 7.1 5.8" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
            '<path d="M7.2 10.8v8.1h9.6v-8.1" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
        ),
        "calendar": (
            '<rect x="5.2" y="6.2" width="13.6" height="12.6" rx="2" fill="none" stroke="{color}" stroke-width="1.7"/>'
            '<path d="M8.2 4.8v2.8M15.8 4.8v2.8M5.4 9.5h13.2" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round"/>'
        ),
        "dollar": (
            '<path d="M12 5.2v13.6M15.2 7.8a3 3 0 0 0-2.8-1.4c-1.9 0-3.2 1-3.2 2.5 0 1.6 1.2 2.2 3.3 2.8 2.1.6 3.7 1.3 3.7 3.2 0 1.9-1.6 3-3.8 3a5.2 5.2 0 0 1-4-1.7" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>'
        ),
    }
    body = icons.get(name, icons["file"]).format(color=color)
    return f"<svg viewBox='0 0 24 24' aria-hidden='true'>{body}</svg>"


def _mode_icon_html(mode_key: str) -> str:
    config = {
        "translation": ("file", "leaseguard-icon-blue"),
        "risks": ("shield", "leaseguard-icon-orange"),
        "help": ("pin", "leaseguard-icon-green"),
    }
    icon_name, css_class = config.get(mode_key, ("file", "leaseguard-icon-blue"))
    return f"<div class='leaseguard-mode-icon {css_class}'>{_icon_svg(icon_name)}</div>"


def _panel_header_html(mode_key: str, title: str) -> str:
    config = {
        "translation": ("file", "leaseguard-icon-blue", "leaseguard-panel-title"),
        "risks": ("shield", "leaseguard-icon-orange", "leaseguard-panel-title leaseguard-risk-title"),
        "help": ("pin", "leaseguard-icon-green", "leaseguard-panel-title leaseguard-help-title"),
    }
    icon_name, icon_class, title_class = config.get(
        mode_key,
        ("file", "leaseguard-icon-blue", "leaseguard-panel-title"),
    )
    return (
        "<div class='leaseguard-panel-header'>"
        f"<div class='leaseguard-panel-icon {icon_class}'>{_icon_svg(icon_name)}</div>"
        f"<div class='{title_class}'>{html.escape(title)}</div>"
        "</div>"
    )


def _panel_section_icon_html(title: str) -> str:
    lowered = title.lower()
    if "rent" in lowered or "fee" in lowered or "payment" in lowered:
        icon_name = "dollar"
        css_class = "leaseguard-icon-blue"
    elif "term" in lowered or "date" in lowered or "renew" in lowered:
        icon_name = "calendar"
        css_class = "leaseguard-icon-orange"
    elif "party" in lowered or "tenant" in lowered or "landlord" in lowered:
        icon_name = "users"
        css_class = "leaseguard-icon-blue"
    elif "repair" in lowered or "maintenance" in lowered:
        icon_name = "shield"
        css_class = "leaseguard-icon-green"
    elif "notice" in lowered or "entry" in lowered or "access" in lowered:
        icon_name = "home"
        css_class = "leaseguard-icon-green"
    else:
        icon_name = "file"
        css_class = "leaseguard-icon-blue"
    return f"<div class='leaseguard-section-icon {css_class}'>{_icon_svg(icon_name)}</div>"


def _zoom_label() -> str:
    return f"{int(round(st.session_state.get('viewer_zoom', 1.0) * 100))}%"


def _zoom_in() -> None:
    _set_viewer_zoom(st.session_state.get("viewer_zoom", 1.0) + 0.15)


def _zoom_out() -> None:
    _set_viewer_zoom(st.session_state.get("viewer_zoom", 1.0) - 0.15)


def _viewer_toolbar_html(results: dict[str, Any]) -> str:
    if _source_mime_type(results) == "application/pdf":
        page_count = _source_page_count(results) or 1
        current_page = _viewer_page(results) + 1
        page_label = f"{current_page} / {page_count}"
    else:
        page_label = "Document"
    return (
        "<div class='leaseguard-viewer-toolbar-row'>"
        f"<div class='leaseguard-toolbar-cluster'>{_icon_svg('file')}<span>{html.escape(page_label)}</span></div>"
        f"<div class='leaseguard-toolbar-cluster'>{_icon_svg('shield')}<span>{html.escape(_zoom_label())}</span></div>"
        "</div>"
    )


def _empty_state_html(message: str) -> str:
    return f"<div class='leaseguard-empty-state'>{html.escape(message)}</div>"


def _translation_cards_html(sections: list[dict[str, str]]) -> str:
    cards: list[str] = []
    for section in sections:
        title = str(section.get("title", "Lease section")).strip() or "Lease section"
        body = " ".join(str(section.get("plain_english", "")).split())
        if len(body) > 520:
            body = body[:517].rsplit(" ", 1)[0] + "..."
        cards.append(
            "<div class='leaseguard-side-card leaseguard-translation-card'>"
            f"<div class='leaseguard-section-row'>{_panel_section_icon_html(title)}"
            f"<div class='leaseguard-section-text'><strong>{html.escape(title)}</strong></div></div>"
            f"<div class='leaseguard-side-copy'>{html.escape(body)}</div>"
            "</div>"
        )
    return "".join(cards)


def _summary_banner_html(lines: list[str]) -> str:
    if not lines:
        return ""
    items = "".join(f"<li>{html.escape(line)}</li>" for line in lines)
    return f"<div class='leaseguard-summary-banner'><strong>Key takeaways</strong><ul>{items}</ul></div>"


def _risk_cards_html(findings: list[dict[str, Any]]) -> str:
    cards: list[str] = []
    for finding in findings[:5]:
        title = str(finding.get("clause_name", "Lease clause")).strip() or "Lease clause"
        severity = finding.get("severity")
        explanation = _compact_explanation(str(finding.get("explanation", "")), sentence_limit=1)
        ask = _negotiation_tip(finding)
        cards.append(
            "<div class='leaseguard-side-card leaseguard-risk-card'>"
            f"{_severity_badge_html(severity)}"
            f"<div class='leaseguard-risk-clause'>{html.escape(title)}</div>"
            f"<div class='leaseguard-side-copy'>{html.escape(explanation)}</div>"
            f"<div class='leaseguard-ask'>Ask for: {html.escape(ask)}</div>"
            "</div>"
        )
    return "".join(cards)


def _resource_cards_html(resources: list[dict[str, str | None]]) -> str:
    cards: list[str] = []
    for resource in resources[:4]:
        name = str(resource.get("name") or "Unknown resource")
        description = " ".join(str(resource.get("description") or "").split())
        actions: list[str] = []
        url = resource.get("url")
        phone = resource.get("phone")
        if url:
            safe_url = html.escape(str(url), quote=True)
            actions.append(f"<a class='leaseguard-resource-link' href='{safe_url}' target='_blank'>Visit website</a>")
        if phone:
            safe_phone = html.escape(str(phone), quote=True)
            actions.append(f"<a class='leaseguard-resource-link' href='tel:{safe_phone}'>{safe_phone}</a>")
        resource_type = str(resource.get("type") or "").strip()
        cards.append(
            "<div class='leaseguard-side-card'>"
            f"<div class='leaseguard-resource-title'><strong>{html.escape(name)}</strong></div>"
            f"{f'<div class=\"leaseguard-resource-type\">{html.escape(resource_type)}</div>' if resource_type else ''}"
            f"<div class='leaseguard-side-copy'>{html.escape(description)}</div>"
            f"{f'<div class=\"leaseguard-resource-actions\">{''.join(actions)}</div>' if actions else ''}"
            "</div>"
        )
    return "".join(cards)


def _report_lines(report: str) -> list[str]:
    lines = [line.rstrip() for line in str(report).splitlines() if line.strip()]
    if lines and lines[0].lower().startswith("improvement decision report"):
        lines = lines[1:]
    return lines


def _report_section_bullets(report: str, heading: str, max_items: int = 4) -> list[str]:
    lines = _report_lines(report)
    bullets: list[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.lower() == heading.lower():
            in_section = True
            continue
        if in_section and not stripped.startswith("-") and re.match(r"^[A-Z][^:]{0,80}(?::.*)?$", stripped):
            break
        if in_section and stripped.startswith("-"):
            bullets.append(stripped.removeprefix("-").strip())
            if len(bullets) >= max_items:
                break
    return bullets


def _best_report_bullets(report: str, max_items: int = 4) -> list[str]:
    for heading in ("Executive Summary", "Prioritized improvements (ranked)"):
        bullets = _report_section_bullets(report, heading, max_items=max_items)
        if bullets:
            return bullets
    return []


def _negotiation_tip(finding: dict[str, Any]) -> str:
    clause_name = str(finding.get("clause_name", "")).lower()
    if "inspection" in clause_name or "access" in clause_name or "entry" in clause_name:
        return "Ask for a written notice window before anyone enters the unit."
    if "fee" in clause_name or "cost" in clause_name or "rent" in clause_name:
        return "Ask for exact dollar limits and a written fee schedule."
    if "relocation" in clause_name or "transfer" in clause_name:
        return "Ask who pays, how long relocation can last, and what rent credit applies."
    if "bug" in clause_name or "treatment" in clause_name:
        return "Ask how responsibilities are split between you and the landlord."
    return "Ask for this clause to be clarified in writing before you sign."


def _looks_like_location_city(candidate: str) -> bool:
    cleaned = re.sub(r"\s+", " ", candidate).strip(" ,")
    if not cleaned:
        return False

    words = [word for word in cleaned.split() if word]
    lowered = cleaned.lower()
    blocked_terms = {
        "state of",
        "county of",
        "city of",
        "local area",
        "landlord",
        "tenant",
        "lease",
        "apartment",
        "dwelling",
        "premises",
        "inspection",
        "moving in",
        "bed bug",
    }
    if len(words) > 4 or len(cleaned) > 32:
        return False
    if any(term in lowered for term in blocked_terms):
        return False
    if cleaned.isupper() and len(words) > 1:
        return False
    letters = re.sub(r"[^A-Za-z]", "", cleaned)
    return len(letters) >= 2


def _extract_location_from_text(text: str) -> tuple[str, str] | None:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return None

    abbr_match = re.search(r"\b([A-Z][A-Za-z .'-]{1,40}),\s*([A-Z]{2})\b", cleaned)
    if abbr_match:
        city = abbr_match.group(1).strip()
        state = abbr_match.group(2).strip().upper()
        if state in STATE_ABBREVIATIONS and _looks_like_location_city(city):
            return city, state

    lowered = cleaned.lower()
    for state_name, abbr in STATE_NAMES_TO_ABBR.items():
        match = re.search(
            rf"\b([A-Z][A-Za-z .'-]{{1,40}}),\s*{re.escape(state_name)}\b",
            cleaned,
            flags=re.IGNORECASE,
        )
        if match:
            candidate_city = match.group(1).strip()
            if _looks_like_location_city(candidate_city):
                return candidate_city, abbr
        match = re.search(
            rf"\b(?:in\s+)?([A-Z][A-Za-z .'-]{{1,40}})\s+{re.escape(state_name)}\b",
            cleaned,
            flags=re.IGNORECASE,
        )
        if match:
            candidate_city = match.group(1).strip()
            if _looks_like_location_city(candidate_city):
                return candidate_city, abbr
        if re.search(rf"\bstate of {re.escape(state_name)}\b", lowered):
            return "Local area", abbr
        if re.search(rf"\b{re.escape(state_name)}\b", lowered):
            return "Local area", abbr

    common_city_fallbacks = {
        "chicago": "IL",
        "seattle": "WA",
    }
    for city_name, abbr in common_city_fallbacks.items():
        if re.search(rf"\b{re.escape(city_name)}\b", lowered):
            return city_name.title(), abbr

    return None


def _live_review_location(location: tuple[str, str]) -> tuple[str, str]:
    city, state_abbr = location
    review_city = city.strip() or "Local area"
    review_state = STATE_ABBR_TO_NAME.get(state_abbr.upper(), state_abbr)
    return review_city, review_state


def _workspace_location(results: dict[str, Any]) -> tuple[str, str] | None:
    stored_location = results.get("resolved_location")
    if isinstance(stored_location, (list, tuple)) and len(stored_location) == 2:
        city, state = str(stored_location[0]).strip(), str(stored_location[1]).strip()
        if city and state:
            return city, state

    for candidate in (
        st.session_state.get("workspace_context", ""),
        str(results.get("cleaned_lease_text", "")),
        str(results.get("report", "")),
    ):
        location = _extract_location_from_text(candidate)
        if location:
            return location
    return None


@st.cache_data(show_spinner=False)
def _pdf_page_count(source_bytes: bytes) -> int:
    import fitz

    document = fitz.open(stream=source_bytes, filetype="pdf")
    try:
        return len(document)
    finally:
        document.close()


@st.cache_data(show_spinner=False)
def _pdf_page_image(source_bytes: bytes, page_index: int, scale: float = 1.35) -> bytes | None:
    try:
        import fitz

        document = fitz.open(stream=source_bytes, filetype="pdf")
        try:
            page = document.load_page(page_index)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            return pixmap.tobytes("png")
        finally:
            document.close()
    except Exception:
        return None


def _source_page_count(results: dict[str, Any]) -> int | None:
    source_bytes = _source_preview_bytes(results)
    if not source_bytes or _source_mime_type(results) != "application/pdf":
        return None
    try:
        return _pdf_page_count(source_bytes)
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def _cached_resource_result(location: str) -> tuple[str, list[dict[str, str | None]], str]:
    return _get_resource_result(location)


def _set_viewer_page(page_index: int) -> None:
    st.session_state["viewer_page"] = max(page_index, 0)



def _set_viewer_page_from_input(input_key: str) -> None:
    try:
        page_number = int(st.session_state.get(input_key, 1))
    except (TypeError, ValueError):
        page_number = 1
    _set_viewer_page(page_number - 1)


def _viewer_page(results: dict[str, Any]) -> int:
    source_name = _source_name(results)
    if st.session_state.get("viewer_source_name") != source_name:
        st.session_state["viewer_source_name"] = source_name
        st.session_state["viewer_page"] = 0

    page_count = _source_page_count(results) or 1
    current_page = min(max(st.session_state.get("viewer_page", 0), 0), page_count - 1)
    st.session_state["viewer_page"] = current_page
    return current_page


def _normalize_match_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", (text or "").lower())
    normalized = re.sub(r"[^a-z0-9 ]", "", normalized)
    return normalized.strip()


@st.cache_data(show_spinner=False)
def _pdf_page_texts(source_bytes: bytes) -> list[str]:
    try:
        import fitz

        document = fitz.open(stream=source_bytes, filetype="pdf")
        try:
            return [_normalize_match_text(page.get_text("text")) for page in document]
        finally:
            document.close()
    except Exception:
        return []


def _section_match_fragments(text: str) -> list[str]:
    fragments: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text or ""):
        fragment = _normalize_match_text(sentence)
        if len(fragment) >= 40:
            fragments.append(fragment)
        if len(fragments) >= 4:
            break

    if fragments:
        return fragments

    normalized = _normalize_match_text(text)
    if not normalized:
        return []

    words = normalized.split()
    if len(words) >= 18:
        return [" ".join(words[:18])]
    return [normalized]


def _page_number_for_section(section: dict[str, Any], page_texts: list[str]) -> int | None:
    original_text = str(section.get("original_text", ""))
    title = str(section.get("title", ""))
    fragments = _section_match_fragments(original_text)
    title_tokens = [token for token in _normalize_match_text(title).split() if len(token) >= 4]
    body_tokens = [token for token in _normalize_match_text(original_text).split() if len(token) >= 5][:24]

    best_page: int | None = None
    best_score = -1
    for page_index, page_text in enumerate(page_texts):
        score = 0
        for fragment in fragments:
            if fragment and fragment in page_text:
                score += 1000 + len(fragment)
        score += sum(6 for token in title_tokens if token in page_text)
        score += sum(2 for token in body_tokens if token in page_text)
        if score > best_score:
            best_score = score
            best_page = page_index

    if best_score <= 0:
        return None
    return best_page


def _sections_for_display(results: dict[str, Any]) -> list[dict[str, Any]]:
    sections = results.get("reader_sections") or []
    if not sections:
        return []

    if _source_mime_type(results) != "application/pdf":
        return sections

    source_bytes = _source_preview_bytes(results)
    if not source_bytes:
        return sections

    page_texts = _pdf_page_texts(source_bytes)
    if not page_texts:
        return sections

    current_page = _viewer_page(results)
    matched_sections: list[dict[str, Any]] = []
    unmatched_sections: list[dict[str, Any]] = []

    for section in sections:
        page_number = _page_number_for_section(section, page_texts)
        section_with_page = dict(section)
        if page_number is None:
            unmatched_sections.append(section_with_page)
            continue
        section_with_page["page_number"] = page_number
        if page_number == current_page:
            matched_sections.append(section_with_page)

    if matched_sections:
        return matched_sections
    if len(unmatched_sections) == len(sections):
        return sections if current_page == 0 else []
    if current_page == 0 and unmatched_sections:
        return unmatched_sections
    return []


def _escape_multiline_text(text: str) -> str:
    escaped = html.escape(text or "")
    return escaped.replace("\n", "<br>")


def _render_password_gate() -> bool:
    app_password = _get_app_password()
    if not app_password:
        return True

    if st.session_state.get("app_authenticated"):
        return True

    st.title("LeaseGuard AI", anchor=False)
    st.write("Enter the shared password to open the app.")

    with st.form("app_password_gate"):
        entered_password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Open app", type="primary", use_container_width=True)

    if submitted:
        if hmac.compare_digest(entered_password, app_password):
            st.session_state["app_authenticated"] = True
            st.session_state["app_auth_error"] = ""
            st.rerun()
        else:
            st.session_state["app_auth_error"] = "Incorrect password."

    if st.session_state.get("app_auth_error"):
        st.error(st.session_state["app_auth_error"])

    return False


def _lookup_term_fallback(term: str) -> tuple[str, str]:
    normalized = term.strip().lower()
    for key, value in TERM_FALLBACKS.items():
        if key == normalized:
            return key.title(), value
    if "joint" in normalized and "liability" in normalized:
        return "Joint and Several Liability", TERM_FALLBACKS["joint and several liability"]
    if "abatement" in normalized:
        return "Abatement", TERM_FALLBACKS["abatement"]
    if "holdover" in normalized:
        return "Holdover Tenancy", TERM_FALLBACKS["holdover tenancy"]
    generic = f"""
## Definition
{term.title()} is a lease concept worth reviewing closely because small wording changes can shift cost, risk, or flexibility between the landlord and tenant.

## How It Works in a Lease
This kind of term usually appears in a clause that defines responsibilities, timelines, or penalties. The exact financial effect depends on the lease wording and local law.

## Financial Implications for Tenants
- It may affect total housing cost, fees, or repair obligations.
- It can change how much leverage a renter has in a dispute.
- Vague language increases the chance of surprise charges later.

## Negotiation Tip
Ask for examples, plain-language definitions, and a written cap on any fee or penalty connected to this clause.
""".strip()
    return term.title(), generic


def _get_term_result(term: str) -> tuple[str, str]:
    if _can_use_live_llm():
        system_prompt = (
            "You are a tenant-rights educator. When given a lease term, respond with exactly "
            "this markdown structure and no extra sections:\n\n"
            "CORRECTED_TERM: <correct term>\n\n"
            "## Definition\n2-3 sentences.\n\n"
            "## How It Works in a Lease\nOne short paragraph.\n\n"
            "## Financial Implications for Tenants\nBullet points.\n\n"
            "## Negotiation Tip\nOne concrete suggestion."
        )
        try:
            result = _call_llm(system_prompt, f"Explain this lease term: {term}")
            first_line, _, rest = result.partition("\n")
            if first_line.startswith("CORRECTED_TERM:"):
                corrected = first_line.replace("CORRECTED_TERM:", "", 1).strip()
                return corrected, rest.lstrip()
            return term.title(), result
        except Exception:
            pass
    return _lookup_term_fallback(term)


def _fallback_resources(location: str) -> tuple[str, list[dict[str, str | None]], str]:
    normalized = location.strip().lower()
    if normalized in RESOURCE_FALLBACKS:
        parts = [part.strip() for part in normalized.split(",")]
        city = parts[0].title()
        state = parts[1].upper() if len(parts) > 1 else ""
        display_location = f"{city}, {state}" if state else city
        return display_location, RESOURCE_FALLBACKS[normalized], "fallback"
    if "chicago" in normalized:
        return "Chicago, IL", RESOURCE_FALLBACKS["chicago, il"], "fallback"
    if "seattle" in normalized:
        return "Seattle, WA", RESOURCE_FALLBACKS["seattle, wa"], "fallback"
    parts = [part.strip() for part in location.split(",") if part.strip()]
    if len(parts) >= 2:
        display = f"{parts[0].title()}, {parts[1].upper()}"
    else:
        display = location.title() if location.strip() else "Your area"
    return display, NATIONAL_RESOURCES, "fallback"


def _get_resource_result(location: str) -> tuple[str, list[dict[str, str | None]], str]:
    if _can_use_live_llm():
        system_prompt = (
            "You are a tenant-rights resource specialist. Given a US location, return "
            "CORRECTED_LOCATION: City, ST on the first line. After that, return a JSON array "
            "with 3-5 real renter advocacy organizations or legal aid providers using keys "
            "name, type, description, url, and phone."
        )
        try:
            raw = _call_llm(system_prompt, f"Location: {location}")
            first_line, _, rest = raw.partition("\n")
            display_location = location
            raw_body = rest.lstrip() if rest else raw
            if first_line.startswith("CORRECTED_LOCATION:"):
                display_location = first_line.replace("CORRECTED_LOCATION:", "", 1).strip()
            parsed = json.loads(raw_body)
            if isinstance(parsed, list):
                return display_location, parsed, "live"
        except Exception:
            pass
    return _fallback_resources(location)


def _render_file_strip(results: dict[str, Any]) -> None:
    source_name = _source_name(results)
    if not source_name:
        return

    page_count = _source_page_count(results)
    source_bytes = _source_preview_bytes(results)
    mime_type = _source_mime_type(results)
    with st.container(border=True):
        info_col, download_col, reset_col = st.columns([7.1, 1.8, 1.1], gap="small")
        with info_col:
            if page_count:
                page_text = f"{page_count} pages"
            elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
                page_text = "DOCX document"
            elif mime_type == "application/pdf":
                page_text = "PDF document"
            else:
                page_text = "Lease file"
            st.markdown(
                f"<div class='leaseguard-file-chip'>{_icon_svg('file')}<span class='leaseguard-file-ok'>&#10003;</span>"
                f"<strong>{html.escape(source_name)}</strong>"
                f"<span>{html.escape(page_text)}</span></div>",
                unsafe_allow_html=True,
            )
        with download_col:
            if source_bytes:
                st.download_button(
                    "Download original",
                    data=source_bytes,
                    file_name=source_name,
                    mime=mime_type or "application/octet-stream",
                    key=f"download_original_{source_name}",
                    use_container_width=True,
                )
        with reset_col:
            if st.button("Start over", key="remove_workspace_file", use_container_width=True):
                _clear_workspace(preserve_context=False)
                st.rerun()


def _render_pdf_document_viewer(results: dict[str, Any]) -> None:
    source_bytes = _source_preview_bytes(results)
    if not source_bytes:
        st.info("Preview unavailable.")
        return

    page_count = _source_page_count(results) or 1
    current_page = _viewer_page(results)
    source_name = _source_name(results)
    page_input_key = f"viewer_page_input_{source_name}"
    if st.session_state.get(page_input_key) != current_page + 1:
        st.session_state[page_input_key] = current_page + 1

    st.markdown(_viewer_toolbar_html(results), unsafe_allow_html=True)

    control_cols = st.columns([0.9, 0.8, 0.7, 0.65, 0.85, 0.65, 0.9], gap="small")
    with control_cols[0]:
        st.button(
            "Back",
            key=f"page_prev_{source_name}",
            use_container_width=True,
            disabled=current_page <= 0,
            on_click=_set_viewer_page,
            args=(current_page - 1,),
        )
    with control_cols[1]:
        st.number_input(
            "Page",
            min_value=1,
            max_value=page_count,
            step=1,
            key=page_input_key,
            label_visibility="collapsed",
            on_change=_set_viewer_page_from_input,
            args=(page_input_key,),
        )
    with control_cols[2]:
        st.markdown(
            f"<div class='leaseguard-page-indicator'>/ {page_count}</div>",
            unsafe_allow_html=True,
        )
    with control_cols[3]:
        st.button("-", key=f"zoom_out_{source_name}", use_container_width=True, on_click=_zoom_out)
    with control_cols[4]:
        st.markdown(f"<div class='leaseguard-page-indicator'>{html.escape(_zoom_label())}</div>", unsafe_allow_html=True)
    with control_cols[5]:
        st.button("+", key=f"zoom_in_{source_name}", use_container_width=True, on_click=_zoom_in)
    with control_cols[6]:
        st.button(
            "Next",
            key=f"page_next_{source_name}",
            use_container_width=True,
            disabled=current_page >= page_count - 1,
            on_click=_set_viewer_page,
            args=(current_page + 1,),
        )

    thumb_col, page_col = st.columns([1.05, 4.2], gap="medium")

    with thumb_col:
        st.markdown("<div class='leaseguard-viewer-toolbar'>Pages</div>", unsafe_allow_html=True)
        with st.container(height=760, border=False):
            for page_index in range(page_count):
                thumb = _pdf_page_image(source_bytes, page_index, scale=0.28)
                with st.container(border=True):
                    if thumb:
                        st.image(thumb, use_container_width=True)
                    st.button(
                        str(page_index + 1),
                        key=f"page_thumb_{source_name}_{page_index}",
                        use_container_width=True,
                        type="primary" if current_page == page_index else "secondary",
                        on_click=_set_viewer_page,
                        args=(page_index,),
                    )

    with page_col:
        page_image = _pdf_page_image(
            source_bytes,
            current_page,
            scale=max(1.05, st.session_state.get("viewer_zoom", 1.0) * 1.1),
        )
        if page_image:
            with st.container(border=True):
                st.image(page_image, use_container_width=True)
        else:
            st.info("Preview unavailable.")


def _render_text_document_viewer(results: dict[str, Any]) -> None:
    text = str(results.get("cleaned_lease_text", "")).strip()
    if not text:
        st.info("Preview unavailable.")
        return

    st.markdown(
        f"<div class='leaseguard-text-doc'>{_escape_multiline_text(text[:7000])}</div>",
        unsafe_allow_html=True,
    )


def _render_document_viewer(results: dict[str, Any]) -> None:
    with st.container(border=True):
        if _source_mime_type(results) == "application/pdf":
            _render_pdf_document_viewer(results)
        else:
            _render_text_document_viewer(results)


def _render_translation_panel(results: dict[str, Any]) -> None:
    st.markdown(_panel_header_html("translation", "Plain English Translation"), unsafe_allow_html=True)
    all_sections = results.get("reader_sections") or []
    if not all_sections:
        st.markdown(_empty_state_html("Upload a lease to see the translation."), unsafe_allow_html=True)
        return

    sections = _sections_for_display(results)
    if not sections:
        st.markdown(_empty_state_html("No translated sections match this page yet."), unsafe_allow_html=True)
        return

    st.markdown(_translation_cards_html(sections), unsafe_allow_html=True)


def _render_risks_panel(results: dict[str, Any]) -> None:
    st.markdown(_panel_header_html("risks", "Risks & Negotiation Tips"), unsafe_allow_html=True)

    findings = results.get("findings", []) or []
    flagged_findings = [item for item in findings if item.get("label") != "fair"]
    report = str(results.get("report", ""))
    review_error = str(results.get("review_error", ""))

    if not flagged_findings:
        if review_error == "missing_location_context":
            message = "Add location context in Additional Context, then analyze again to load risk review."
        elif review_error in {"chroma_index_missing", "chroma_runtime_bootstrap_failed", "live_stack_unavailable"}:
            message = "Live review is not configured on this copy yet."
        elif review_error == "openai_connection_failed":
            message = "Risk review could not reach the AI service."
        elif review_error == "live_review_failed":
            message = "Risk review could not be generated for this file."
        else:
            message = "Analyze the lease to load risk review."
        st.markdown(_empty_state_html(message), unsafe_allow_html=True)
        return

    ranked_findings = sorted(flagged_findings, key=lambda item: item.get("severity") or 0, reverse=True)
    summary_lines = [
        _compact_explanation(item, sentence_limit=1)
        for item in _best_report_bullets(report, max_items=3)
    ]
    if summary_lines:
        st.markdown(_summary_banner_html(summary_lines), unsafe_allow_html=True)

    st.markdown(_risk_cards_html(ranked_findings), unsafe_allow_html=True)

    if report:
        with st.expander("See full review notes"):
            st.markdown(report)
        st.download_button(
            "Download review summary",
            data=report,
            file_name="leaseguard-analysis.md",
            mime="text/markdown",
            use_container_width=True,
        )


def _render_help_panel(results: dict[str, Any]) -> None:
    st.markdown(_panel_header_html("help", "Local Help"), unsafe_allow_html=True)
    location = _workspace_location(results)
    if location:
        display_location, resources, _ = _cached_resource_result(f"{location[0]}, {location[1]}")
    else:
        display_location, resources = "National resources", NATIONAL_RESOURCES

    st.markdown(f"<div class='leaseguard-kicker'>{html.escape(display_location)}</div>", unsafe_allow_html=True)
    st.markdown(_resource_cards_html(resources), unsafe_allow_html=True)


def _render_workspace_content(results: dict[str, Any], panel_renderer) -> None:
    _render_document_viewer(results)
    st.markdown("<div style='height: 1rem;'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        panel_renderer(results)



def _render_workspace_modes(results: dict[str, Any]) -> None:
    panel_renderer = {
        "translation": _render_translation_panel,
        "risks": _render_risks_panel,
        "help": _render_help_panel,
    }.get(st.session_state.get("active_mode", "translation"), _render_translation_panel)

    _render_document_viewer(results)

    modes = [
        ("translation", "Plain English Translation"),
        ("risks", "Risks & Negotiation Tips"),
        ("help", "Local Help"),
    ]
    st.markdown("<div style='height: 1rem;'></div>", unsafe_allow_html=True)
    mode_cols = st.columns(3, gap="medium")
    active_mode = st.session_state.get("active_mode", "translation")
    for column, (mode_key, label) in zip(mode_cols, modes):
        with column:
            with st.container(border=True):
                st.markdown(_mode_icon_html(mode_key), unsafe_allow_html=True)
                st.button(
                    label,
                    key=f"workspace_mode_{mode_key}",
                    use_container_width=True,
                    type="primary" if active_mode == mode_key else "secondary",
                    on_click=_set_active_mode,
                    args=(mode_key,),
                )

    st.markdown("<div style='height: 1rem;'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        panel_renderer(results)


def _render_glossary() -> None:
    with st.expander("Look up a lease term", expanded=False):
        chip_cols = st.columns(4)
        for index, term in enumerate(COMMON_TERMS):
            with chip_cols[index % 4]:
                if st.button(term, key=f"term_{index}", use_container_width=True):
                    st.session_state["pending_term"] = term

        with st.form("term_lookup", clear_on_submit=True):
            typed_term = st.text_input(
                "Enter a lease term",
                placeholder="Example: escalation clause",
            )
            submitted = st.form_submit_button("Explain term")

        active_term = st.session_state.get("pending_term") or (typed_term if submitted else None)
        st.session_state["pending_term"] = None

        if active_term:
            corrected_term, body = _get_term_result(active_term)
            st.session_state["last_term"] = corrected_term
            st.session_state["last_result"] = body

        if st.session_state.get("last_result"):
            st.markdown(f"**{st.session_state['last_term']}**")
            st.markdown(st.session_state["last_result"])


def _resolve_review_location(context: str, extracted_text: str) -> tuple[str, str] | None:
    return _extract_location_from_text(context) or _extract_location_from_text(extracted_text)


def _run_workspace_analysis(uploaded_file: Any, full_review_available: bool) -> None:
    if not uploaded_file:
        st.warning("Add a PDF or DOCX lease to continue.")
        return

    uploaded_bytes = uploaded_file.getvalue()
    suffix = Path(uploaded_file.name).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_file.write(uploaded_bytes)
        temp_path = temp_file.name

    try:
        extracted_text = extract_lease_text(temp_path)
    except LeaseTextExtractionError as exc:
        Path(temp_path).unlink(missing_ok=True)
        st.error(str(exc))
        return
    except Exception:
        extracted_text = ""

    resolved_location = _resolve_review_location(st.session_state.get("workspace_context", ""), extracted_text)
    live_status = _get_capabilities()["full_live_audit"]

    with st.status("Analyzing lease...", expanded=True) as status:
        progress_slot = st.empty()

        def _on_stage(message: str) -> None:
            progress_slot.write(message)

        try:
            if full_review_available and resolved_location:
                review_city, review_state = _live_review_location(resolved_location)
                results = run_live_pipeline(
                    file_path=temp_path,
                    city=review_city,
                    state=review_state,
                    on_stage=_on_stage,
                )
                results["review_mode"] = "live_rag"
                results["review_error"] = ""
                results["resolved_location"] = resolved_location
                _attach_uploaded_source_metadata(
                    results,
                    source_name=uploaded_file.name,
                    source_bytes=uploaded_bytes,
                    source_mime_type=uploaded_file.type,
                )
                st.session_state["audit_results"] = results
                st.session_state["audit_source"] = "live"
                st.session_state["reader_results"] = copy.deepcopy(results)
                st.session_state["reader_source"] = "live"
            else:
                results = run_reader_pipeline(
                    file_path=temp_path,
                    on_stage=_on_stage,
                )
                if resolved_location:
                    results["resolved_location"] = resolved_location
                results["review_error"] = "missing_location_context" if live_status["available"] else str(live_status.get("error") or "live_stack_unavailable")
                _attach_uploaded_source_metadata(
                    results,
                    source_name=uploaded_file.name,
                    source_bytes=uploaded_bytes,
                    source_mime_type=uploaded_file.type,
                )
                st.session_state["audit_results"] = None
                st.session_state["audit_source"] = None
                st.session_state["reader_results"] = copy.deepcopy(results)
                st.session_state["reader_source"] = "upload"
            st.session_state["active_mode"] = "translation"
            st.session_state["viewer_source_name"] = ""
            st.session_state["viewer_page"] = 0
            st.session_state["viewer_zoom"] = 1.0
            status.update(label="Lease ready", state="complete")
        except Exception as exc:
            try:
                fallback_results = run_reader_pipeline(
                    file_path=temp_path,
                    on_stage=_on_stage,
                )
                if resolved_location:
                    fallback_results["resolved_location"] = resolved_location
                fallback_results["review_error"] = _normalize_review_error(exc)
                _attach_uploaded_source_metadata(
                    fallback_results,
                    source_name=uploaded_file.name,
                    source_bytes=uploaded_bytes,
                    source_mime_type=uploaded_file.type,
                )
                st.session_state["audit_results"] = None
                st.session_state["audit_source"] = None
                st.session_state["reader_results"] = copy.deepcopy(fallback_results)
                st.session_state["reader_source"] = "upload"
                st.session_state["active_mode"] = "translation"
                st.session_state["viewer_source_name"] = ""
                st.session_state["viewer_page"] = 0
                st.session_state["viewer_zoom"] = 1.0
                status.update(label="Lease ready", state="complete")
            except Exception:
                status.update(label="Lease unavailable", state="error")
                st.error("LeaseGuard couldn't open that file.")
        finally:
            Path(temp_path).unlink(missing_ok=True)


def _render_top_shell() -> None:
    st.markdown(
        f"""
        <div class="leaseguard-topbar">
          <div class="leaseguard-brandmark">{_icon_svg('brand')}</div>
          <div class="leaseguard-brandtext">LeaseGuard AI</div>
        </div>
        <div class="leaseguard-hero">
          <h1>Understand Your Lease Before You Sign</h1>
          <p>Upload a lease and get a plain-English translation, negotiation tips, and local housing resources.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_upload_card() -> None:
    full_review_available = _get_capabilities()["full_live_audit"]["available"]
    upload_key = f"workspace_upload_{st.session_state.get('upload_key_version', 0)}"

    with st.container(border=True):
        upload_col, context_col = st.columns([1.05, 1.0], gap="large")
        with upload_col:
            uploaded_file = st.file_uploader(
                "Drop your lease here or browse files",
                type=["pdf", "docx"],
                key=upload_key,
            )
            st.caption("PDF, DOCX up to 25MB")
        with context_col:
            st.text_area(
                "Additional Context (Optional)",
                key="workspace_context",
                placeholder="Example: I'm moving to Chicago and I'm concerned about pet fees and early termination penalties.",
                height=160,
            )

        action_cols = st.columns([1, 1], gap="medium")
        with action_cols[0]:
            if st.button("Analyze Lease", type="primary", use_container_width=True, key="analyze_workspace"):
                _run_workspace_analysis(uploaded_file, full_review_available)
        with action_cols[1]:
            if st.button("Use Sample Lease", use_container_width=True, key="workspace_sample"):
                _load_sample_audit()

        st.caption("Your documents are secure and private. We never share your data.")


def _render_workspace_page() -> None:
    _render_top_shell()
    _render_upload_card()
    results, _source = _current_results()
    if not results:
        return

    _render_file_strip(results)
    _render_workspace_modes(results)


def _apply_styles() -> None:
    st.set_page_config(
        page_title="LeaseGuard AI",
        page_icon=":house:",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.markdown(
        """
        <style>
        :root {
            --line: #d9e3f0;
            --blue: #2554f4;
            --blue-soft: #edf3ff;
            --orange: #f28a1a;
            --orange-soft: #fff4e8;
            --green: #19a463;
            --green-soft: #ebfbf3;
            --ink: #12243a;
            --muted: #6b7b93;
            --surface: #ffffff;
            --shadow: 0 16px 40px rgba(18, 36, 58, 0.08);
        }

        .stApp {
            background:
                radial-gradient(circle at top center, rgba(37, 84, 244, 0.08), transparent 30%),
                #f4f7fb;
        }

        .block-container {
            max-width: 1220px;
            padding-top: 0.9rem;
            padding-bottom: 3.25rem;
        }

        [data-testid="stSidebar"],
        [data-testid="stSidebarCollapsedControl"] {
            display: none;
        }

        div[data-testid="stExpander"],
        div[data-testid="stVerticalBlockBorderWrapper"] > div[data-testid="stVerticalBlock"] {
            border: 1px solid var(--line);
            border-radius: 22px;
            background: white;
            box-shadow: 0 10px 24px rgba(18, 36, 58, 0.05);
        }

        div[data-testid="stFileUploader"] section {
            border-radius: 24px;
            border: 1.5px dashed rgba(37, 84, 244, 0.35);
            background: linear-gradient(180deg, rgba(37, 84, 244, 0.04), rgba(37, 84, 244, 0.00));
            min-height: 220px;
        }

        div[data-testid="stFileUploader"] label {
            color: var(--ink);
            font-weight: 600;
        }

        .stButton > button,
        .stDownloadButton > button,
        .stFormSubmitButton > button {
            min-height: 2.9rem;
            border-radius: 12px;
            border: 1px solid rgba(203, 214, 231, 0.95);
            font-weight: 600;
            box-shadow: none;
            font-size: 0.98rem;
            background: #ffffff;
            transition: border-color 120ms ease, box-shadow 120ms ease, transform 120ms ease;
        }

        .stButton > button:hover,
        .stDownloadButton > button:hover,
        .stFormSubmitButton > button:hover {
            border-color: rgba(114, 140, 198, 0.9);
            box-shadow: 0 6px 16px rgba(18, 36, 58, 0.06);
        }

        .stButton > button[kind="primary"],
        .stFormSubmitButton > button[kind="primary"] {
            background: #2754ff;
            color: white;
            border: 1px solid #2754ff;
        }

        .stButton > button[kind="secondary"],
        .stDownloadButton > button {
            background: #ffffff;
            color: var(--ink);
        }

        .stTextArea textarea,
        .stTextInput input,
        div[data-testid="stNumberInput"] input {
            border-radius: 14px !important;
            border: 1px solid var(--line) !important;
        }

        .leaseguard-topbar {
            display: flex;
            align-items: center;
            gap: 0.85rem;
            padding: 0.8rem 0.25rem 1.1rem;
            border-bottom: 1px solid rgba(219, 228, 241, 0.8);
        }

        .leaseguard-brandmark {
            width: 2.5rem;
            height: 2.5rem;
            border-radius: 999px;
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--blue);
            background: rgba(39, 84, 255, 0.10);
            font-size: 0.95rem;
            font-weight: 700;
        }

        .leaseguard-brandtext {
            font-size: 1.85rem;
            font-weight: 700;
            color: var(--blue);
        }

        .leaseguard-hero {
            text-align: center;
            padding: 2.7rem 0 1.85rem;
        }

        .leaseguard-hero h1 {
            margin: 0;
            color: var(--ink);
            font-size: 3.45rem;
            line-height: 1.05;
            font-weight: 760;
        }

        .leaseguard-hero p {
            margin: 0.8rem auto 0;
            max-width: 46rem;
            color: var(--muted);
            font-size: 1.28rem;
            line-height: 1.5;
        }

        .leaseguard-file-chip {
            display: flex;
            align-items: center;
            gap: 0.8rem;
            color: var(--ink);
            min-height: 3.25rem;
        }

        .leaseguard-file-chip strong {
            font-size: 1.05rem;
        }

        .leaseguard-file-chip span:last-child {
            color: var(--muted);
            margin-left: 0.25rem;
        }

        .leaseguard-file-ok {
            color: #14a44d;
            font-size: 1rem;
        }

        .leaseguard-viewer-toolbar,
        .leaseguard-page-indicator {
            color: var(--muted);
            font-weight: 600;
            padding: 0.1rem 0 0.75rem;
            text-align: center;
        }

        .leaseguard-panel-header {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin-bottom: 1rem;
        }

        .leaseguard-panel-icon {
            width: 2.35rem;
            height: 2.35rem;
            border-radius: 999px;
            display: flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 auto;
        }

        .leaseguard-panel-icon svg {
            width: 1.05rem;
            height: 1.05rem;
            display: block;
        }

        .leaseguard-panel-title {
            font-size: 1.6rem;
            font-weight: 700;
            color: var(--blue);
            margin-bottom: 0;
        }

        .leaseguard-risk-title {
            color: var(--orange);
        }

        .leaseguard-help-title {
            color: var(--green);
        }

        .leaseguard-text-doc {
            max-height: 58rem;
            overflow-y: auto;
            padding: 1.45rem;
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 18px;
            color: var(--ink);
            line-height: 1.75;
        }

        .leaseguard-mode-icon {
            width: 2.8rem;
            height: 2.8rem;
            border-radius: 999px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            margin-bottom: 0.6rem;
        }

        .leaseguard-icon-blue {
            background: var(--blue-soft);
            color: var(--blue);
        }

        .leaseguard-icon-orange {
            background: var(--orange-soft);
            color: var(--orange);
        }

        .leaseguard-icon-green {
            background: var(--green-soft);
            color: var(--green);
        }

        .leaseguard-mode-copy {
            color: var(--muted);
            font-size: 0.98rem;
            line-height: 1.55;
            margin-top: 0.55rem;
        }

        .leaseguard-translation-card {
            padding-top: 1rem;
        }

        .leaseguard-section-row {
            display: flex;
            align-items: center;
            gap: 0.8rem;
            margin-bottom: 0.45rem;
        }

        .leaseguard-section-icon {
            width: 2rem;
            height: 2rem;
            border-radius: 999px;
            display: flex;
            align-items: center;
            justify-content: center;
            background: var(--blue-soft);
            color: var(--blue);
            font-size: 0.85rem;
            font-weight: 700;
            flex: 0 0 auto;
        }

        .leaseguard-section-text {
            color: var(--ink);
            line-height: 1.35;
        }

        .leaseguard-risk-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.55rem;
            border-radius: 999px;
            padding: 0.42rem 0.78rem;
            font-size: 0.92rem;
            font-weight: 700;
            margin-bottom: 0.7rem;
        }

        .leaseguard-risk-badge span {
            opacity: 0.9;
        }

        .leaseguard-risk-high {
            background: #fdecec;
            color: #cf3d3d;
        }

        .leaseguard-risk-medium {
            background: #fff4df;
            color: #c77700;
        }

        .leaseguard-risk-low {
            background: #ecf8f1;
            color: #198b57;
        }

        .leaseguard-risk-review {
            background: #eef2f7;
            color: #5f738f;
        }

        .leaseguard-ask {
            color: var(--ink);
            font-weight: 600;
            margin-top: 0.7rem;
            line-height: 1.45;
        }

        .leaseguard-side-card {
            border: 1px solid var(--line);
            border-radius: 20px;
            background: #ffffff;
            padding: 1.05rem 1.1rem;
            margin-bottom: 0.9rem;
        }

        .leaseguard-side-copy {
            color: var(--muted);
            font-size: 0.98rem;
            line-height: 1.65;
        }

        .leaseguard-risk-clause {
            color: var(--ink);
            font-size: 1.06rem;
            font-weight: 700;
            line-height: 1.4;
            margin-bottom: 0.45rem;
        }

        .leaseguard-empty-state {
            border: 1px solid var(--line);
            border-radius: 18px;
            background: #f8fbff;
            color: var(--muted);
            padding: 1rem 1.05rem;
            line-height: 1.6;
        }

        .leaseguard-summary-banner {
            border: 1px solid rgba(242, 138, 26, 0.22);
            border-radius: 20px;
            background: linear-gradient(180deg, rgba(242, 138, 26, 0.08), rgba(255, 255, 255, 0.96));
            padding: 1rem 1.1rem;
            margin-bottom: 1rem;
        }

        .leaseguard-summary-banner strong {
            display: block;
            color: var(--ink);
            margin-bottom: 0.45rem;
        }

        .leaseguard-summary-banner ul {
            margin: 0;
            padding-left: 1.15rem;
            color: var(--muted);
            line-height: 1.6;
        }

        .leaseguard-resource-title {
            color: var(--ink);
            margin-bottom: 0.2rem;
        }

        .leaseguard-resource-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 0.6rem;
            margin-top: 0.85rem;
        }

        .leaseguard-resource-link {
            display: inline-flex;
            align-items: center;
            gap: 0.35rem;
            border-radius: 999px;
            border: 1px solid var(--line);
            background: #f8fbff;
            color: var(--ink) !important;
            text-decoration: none !important;
            font-size: 0.92rem;
            font-weight: 600;
            padding: 0.45rem 0.8rem;
        }

        .leaseguard-kicker {
            color: var(--muted);
            font-size: 0.9rem;
            font-weight: 700;
            letter-spacing: 0.02em;
            text-transform: uppercase;
            margin-bottom: 0.6rem;
        }

        .leaseguard-resource-type {
            display: inline-block;
            border-radius: 999px;
            padding: 0.25rem 0.65rem;
            background: var(--green-soft);
            color: var(--green);
            font-size: 0.84rem;
            font-weight: 700;
            margin-bottom: 0.4rem;
        }

        .leaseguard-brandmark svg,
        .leaseguard-mode-icon svg,
        .leaseguard-section-icon svg,
        .leaseguard-file-chip svg,
        .leaseguard-toolbar-cluster svg {
            width: 1.2rem;
            height: 1.2rem;
            display: block;
        }

        .leaseguard-brandmark svg {
            width: 1.45rem;
            height: 1.45rem;
        }

        .leaseguard-mode-icon svg {
            width: 1.38rem;
            height: 1.38rem;
        }

        .leaseguard-section-icon svg {
            width: 1rem;
            height: 1rem;
        }

        .leaseguard-file-chip svg {
            color: var(--muted);
            flex: 0 0 auto;
        }

        .leaseguard-viewer-toolbar-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.75rem;
            padding: 0 0 0.85rem;
            border-bottom: 1px solid rgba(217, 227, 240, 0.9);
            margin-bottom: 0.85rem;
        }

        div[data-testid="stSegmentedControl"] {
            margin: 1.15rem 0 1.35rem;
        }

        div[data-testid="stSegmentedControl"] [data-baseweb="button-group"] {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.9rem;
            width: 100%;
        }

        div[data-testid="stSegmentedControl"] [data-baseweb="button-group"] button {
            min-height: 5rem;
            height: auto;
            border-radius: 22px !important;
            border: 1px solid var(--line) !important;
            background: #ffffff !important;
            color: var(--ink) !important;
            box-shadow: 0 10px 24px rgba(18, 36, 58, 0.04);
            padding: 0.95rem 1rem !important;
        }

        div[data-testid="stSegmentedControl"] [data-baseweb="button-group"] button[aria-checked="true"],
        div[data-testid="stSegmentedControl"] [data-baseweb="button-group"] button[aria-pressed="true"] {
            color: var(--blue) !important;
            border-color: rgba(37, 84, 244, 0.45) !important;
            box-shadow: 0 0 0 2px rgba(37, 84, 244, 0.12);
        }

        div[data-testid="stSegmentedControl"] [data-baseweb="button-group"] button div {
            white-space: normal !important;
            line-height: 1.28;
            font-weight: 650;
        }

        .leaseguard-toolbar-cluster {
            display: inline-flex;
            align-items: center;
            gap: 0.55rem;
            color: var(--muted);
            font-size: 0.95rem;
            font-weight: 600;
        }

        .leaseguard-page-indicator {
            color: var(--ink);
            font-weight: 700;
            padding: 0.6rem 0.75rem;
            text-align: center;
            border: 1px solid var(--line);
            border-radius: 14px;
            background: #f9fbff;
        }

        div[data-testid="stImage"] img {
            border-radius: 14px;
        }

        @media (max-width: 900px) {
            .leaseguard-hero h1 {
                font-size: 2.4rem;
            }

            .leaseguard-hero p {
                font-size: 1.1rem;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def main() -> None:
    _apply_styles()
    _init_state()
    if not _render_password_gate():
        st.stop()
    _render_workspace_page()


if __name__ == "__main__":
    main()
