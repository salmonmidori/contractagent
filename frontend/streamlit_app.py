"""Streamlit front end for the LeaseGuard AI project."""

from __future__ import annotations

import copy
import hmac
import html
import importlib.util
import json
from mimetypes import guess_type
import re
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

try:
    from frontend.audit_backend import (
        extract_lease_text,
        run_live_pipeline,
        run_reader_pipeline,
        run_sample_pipeline,
    )
    from frontend.secrets_utils import get_secret
except ImportError:
    from audit_backend import extract_lease_text, run_live_pipeline, run_reader_pipeline, run_sample_pipeline
    from secrets_utils import get_secret


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


def _get_capabilities() -> dict[str, Any]:
    live_llm_ready = _can_use_live_llm()
    requirements = {
        "LangChain OpenAI": _module_available("langchain_openai"),
        "LlamaIndex": _module_available("llama_index"),
        "ChromaDB": _module_available("chromadb"),
        "Chroma index": CHROMADB_PATH.exists() and any(CHROMADB_PATH.iterdir()),
        "DOCX parser": _module_available("docx2txt"),
        "PDF parser": _module_available("pypdf"),
    }
    missing = [name for name, available in requirements.items() if not available]

    return {
        "sample_review": {"available": True},
        "live_llm": {"available": live_llm_ready},
        "full_live_audit": {
            "available": live_llm_ready and not missing,
            "missing": missing,
        },
    }


def _init_state() -> None:
    st.session_state.setdefault("app_authenticated", False)
    st.session_state.setdefault("app_auth_error", "")
    st.session_state.setdefault("active_mode", "translation")
    st.session_state.setdefault("upload_key_version", 0)
    st.session_state.setdefault("viewer_page", 0)
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


def _clear_workspace(preserve_context: bool = True) -> None:
    st.session_state["audit_results"] = None
    st.session_state["audit_source"] = None
    st.session_state["reader_results"] = None
    st.session_state["reader_source"] = None
    st.session_state["viewer_page"] = 0
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


def _mode_icon_html(mode_key: str) -> str:
    config = {
        "translation": ("T", "leaseguard-icon-blue"),
        "risks": ("R", "leaseguard-icon-orange"),
        "help": ("H", "leaseguard-icon-green"),
    }
    symbol, css_class = config.get(mode_key, ("L", "leaseguard-icon-blue"))
    return f"<div class='leaseguard-mode-icon {css_class}'>{symbol}</div>"


def _panel_section_icon(title: str) -> str:
    lowered = title.lower()
    if "rent" in lowered or "fee" in lowered or "payment" in lowered:
        return "$"
    if "term" in lowered or "date" in lowered or "renew" in lowered:
        return "T"
    if "party" in lowered or "tenant" in lowered or "landlord" in lowered:
        return "P"
    if "pet" in lowered:
        return "P"
    if "repair" in lowered or "maintenance" in lowered:
        return "M"
    if "notice" in lowered or "entry" in lowered or "access" in lowered:
        return "N"
    return "L"


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


def _extract_location_from_text(text: str) -> tuple[str, str] | None:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return None

    abbr_match = re.search(r"\b([A-Z][A-Za-z .'-]{1,40}),\s*([A-Z]{2})\b", cleaned)
    if abbr_match:
        city = abbr_match.group(1).strip()
        state = abbr_match.group(2).strip().upper()
        if state in STATE_ABBREVIATIONS:
            return city, state

    lowered = cleaned.lower()
    for state_name, abbr in STATE_NAMES_TO_ABBR.items():
        match = re.search(rf"\b([A-Z][A-Za-z .'-]{{1,40}}),\s*{re.escape(state_name)}\b", cleaned, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(), abbr
        if state_name in lowered and "chicago" in lowered:
            return "Chicago", abbr
    return None


def _workspace_location(results: dict[str, Any]) -> tuple[str, str] | None:
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


def _viewer_page(results: dict[str, Any]) -> int:
    source_name = _source_name(results)
    if st.session_state.get("viewer_source_name") != source_name:
        st.session_state["viewer_source_name"] = source_name
        st.session_state["viewer_page"] = 0

    page_count = _source_page_count(results) or 1
    current_page = min(max(st.session_state.get("viewer_page", 0), 0), page_count - 1)
    st.session_state["viewer_page"] = current_page
    return current_page


def _sections_for_display(results: dict[str, Any]) -> list[dict[str, str]]:
    sections = results.get("reader_sections") or []
    if not sections:
        return []

    page_count = _source_page_count(results) or 1
    current_page = _viewer_page(results) if _source_mime_type(results) == "application/pdf" else 0
    group_size = max(1, (len(sections) + page_count - 1) // page_count)
    start = current_page * group_size
    end = min(start + group_size + 1, len(sections))
    return sections[start:end] or sections[:5]


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
        info_col, download_col, replace_col, remove_col = st.columns([5.6, 1.7, 1.5, 1.0], gap="medium")
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
                f"<div class='leaseguard-file-chip'><span class='leaseguard-file-ok'>&#10003;</span>"
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
        with replace_col:
            if st.button("Replace file", key="replace_workspace_file", use_container_width=True):
                _clear_workspace(preserve_context=True)
                st.rerun()
        with remove_col:
            if st.button("Clear", key="remove_workspace_file", use_container_width=True):
                _clear_workspace(preserve_context=False)
                st.rerun()


def _render_pdf_document_viewer(results: dict[str, Any]) -> None:
    source_bytes = _source_preview_bytes(results)
    if not source_bytes:
        st.info("Preview unavailable.")
        return

    page_count = _source_page_count(results) or 1
    current_page = _viewer_page(results)
    thumb_col, page_col = st.columns([1.05, 4.2], gap="medium")

    with thumb_col:
        st.markdown("<div class='leaseguard-viewer-toolbar'>Pages</div>", unsafe_allow_html=True)
        for page_index in range(page_count):
            thumb = _pdf_page_image(source_bytes, page_index, scale=0.28)
            with st.container(border=True):
                if thumb:
                    st.image(thumb, use_container_width=True)
                st.button(
                    str(page_index + 1),
                    key=f"page_thumb_{_source_name(results)}_{page_index}",
                    use_container_width=True,
                    type="primary" if current_page == page_index else "secondary",
                    on_click=_set_viewer_page,
                    args=(page_index,),
                )

    with page_col:
        nav_cols = st.columns([1, 2, 1])
        with nav_cols[0]:
            st.button(
                "Previous",
                key=f"page_prev_{_source_name(results)}",
                use_container_width=True,
                disabled=current_page <= 0,
                on_click=_set_viewer_page,
                args=(current_page - 1,),
            )
        with nav_cols[1]:
            st.markdown(
                f"<div class='leaseguard-page-indicator'>{current_page + 1} / {page_count}</div>",
                unsafe_allow_html=True,
            )
        with nav_cols[2]:
            st.button(
                "Next",
                key=f"page_next_{_source_name(results)}",
                use_container_width=True,
                disabled=current_page >= page_count - 1,
                on_click=_set_viewer_page,
                args=(current_page + 1,),
            )

        page_image = _pdf_page_image(source_bytes, current_page, scale=1.2)
        if page_image:
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
    st.markdown("<div class='leaseguard-panel-title'>Plain English Translation</div>", unsafe_allow_html=True)
    sections = _sections_for_display(results)
    if not sections:
        st.info("Upload a lease to see the translation.")
        return

    for section in sections[:6]:
        with st.container(border=True):
            title = str(section.get("title", "Lease section"))
            st.markdown(
                f"<div class='leaseguard-section-row'>"
                f"<div class='leaseguard-section-icon'>{_panel_section_icon(title)}</div>"
                f"<div class='leaseguard-section-text'><strong>{html.escape(title)}</strong></div>"
                "</div>",
                unsafe_allow_html=True,
            )
            st.write(section.get("plain_english", ""))


def _render_risks_panel(results: dict[str, Any]) -> None:
    st.markdown("<div class='leaseguard-panel-title leaseguard-risk-title'>Risks &amp; Negotiation Tips</div>", unsafe_allow_html=True)

    findings = results.get("findings", []) or []
    flagged_findings = [item for item in findings if item.get("label") != "fair"]
    report = str(results.get("report", ""))

    if not flagged_findings:
        st.info("Add the lease location in Additional Context to unlock the risk review for this file.")
        return

    ranked_findings = sorted(flagged_findings, key=lambda item: item.get("severity") or 0, reverse=True)
    top_finding = ranked_findings[0]
    with st.container(border=True):
        st.markdown("<div class='leaseguard-kicker'>Top issue</div>", unsafe_allow_html=True)
        st.markdown(f"**{top_finding.get('clause_name', 'Lease clause')}**")
        st.write(_compact_explanation(str(top_finding.get("explanation", "")), sentence_limit=1))

    for finding in ranked_findings[:5]:
        severity = finding.get("severity")
        with st.container(border=True):
            st.markdown(_severity_badge_html(severity), unsafe_allow_html=True)
            st.markdown(f"**{finding.get('clause_name', 'Lease clause')}**")
            st.write(_compact_explanation(str(finding.get("explanation", "")), sentence_limit=1))
            st.markdown(f"<div class='leaseguard-ask'>Ask for: {html.escape(_negotiation_tip(finding))}</div>", unsafe_allow_html=True)

    if report:
        with st.expander("Open detailed review"):
            st.markdown(report)
        st.download_button(
            "Download review summary",
            data=report,
            file_name="leaseguard-analysis.md",
            mime="text/markdown",
            use_container_width=True,
        )


def _render_help_panel(results: dict[str, Any]) -> None:
    st.markdown("<div class='leaseguard-panel-title leaseguard-help-title'>Local Help</div>", unsafe_allow_html=True)
    location = _workspace_location(results)
    if location:
        display_location, resources, _ = _cached_resource_result(f"{location[0]}, {location[1]}")
    else:
        display_location, resources = "Your area", NATIONAL_RESOURCES

    st.markdown(f"<div class='leaseguard-kicker'>{html.escape(display_location)}</div>", unsafe_allow_html=True)
    for resource in resources[:4]:
        with st.container(border=True):
            st.markdown(f"**{resource.get('name', 'Unknown resource')}**")
            if resource.get("type"):
                st.markdown(
                    f"<div class='leaseguard-resource-type'>{html.escape(str(resource['type']))}</div>",
                    unsafe_allow_html=True,
                )
            st.write(resource.get("description", ""))
            if resource.get("url"):
                st.markdown(f"[Visit website]({resource['url']})")
            if resource.get("phone"):
                st.write(f"Phone: {resource['phone']}")


def _render_workspace_modes(results: dict[str, Any]) -> None:
    modes = [
        ("translation", "Plain English Translation", "See your lease, clause by clause, in everyday language."),
        ("risks", "Risks & Negotiation Tips", "Identify concerning clauses and learn what to negotiate."),
        ("help", "Local Help", "Find tenant resources, legal aid, and housing assistance nearby."),
    ]
    mode_cols = st.columns(3, gap="medium")
    for column, (mode_key, label, description) in zip(mode_cols, modes):
        with column:
            with st.container(border=True):
                st.markdown(_mode_icon_html(mode_key), unsafe_allow_html=True)
                st.button(
                    label,
                    key=f"workspace_mode_{mode_key}",
                    use_container_width=True,
                    type="primary" if st.session_state.get("active_mode") == mode_key else "secondary",
                    on_click=_set_active_mode,
                    args=(mode_key,),
                )
                st.markdown(f"<div class='leaseguard-mode-copy'>{html.escape(description)}</div>", unsafe_allow_html=True)

    left_col, right_col = st.columns([1.7, 1.05], gap="large")
    with left_col:
        _render_document_viewer(results)

    with right_col:
        active_mode = st.session_state.get("active_mode", "translation")
        if active_mode == "translation":
            _render_translation_panel(results)
        elif active_mode == "risks":
            _render_risks_panel(results)
        else:
            _render_help_panel(results)


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
    except Exception:
        extracted_text = ""

    location = _resolve_review_location(st.session_state.get("workspace_context", ""), extracted_text)

    with st.status("Analyzing lease...", expanded=True) as status:
        progress_slot = st.empty()

        def _on_stage(message: str) -> None:
            progress_slot.write(message)

        try:
            if full_review_available and location:
                city, state = location
                results = run_live_pipeline(
                    file_path=temp_path,
                    city=city,
                    state=state,
                    on_stage=_on_stage,
                )
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
            status.update(label="Lease ready", state="complete")
            if full_review_available and not location:
                st.info("Add the lease city and state in Additional Context to unlock risk review and local help.")
        except Exception:
            status.update(label="Lease unavailable", state="error")
            st.error("LeaseGuard couldn't open that file.")
        finally:
            Path(temp_path).unlink(missing_ok=True)


def _render_top_shell() -> None:
    st.markdown(
        """
        <div class="leaseguard-topbar">
          <div class="leaseguard-brandmark">LG</div>
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
                radial-gradient(circle at top center, rgba(37, 84, 244, 0.10), transparent 28%),
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
            box-shadow: var(--shadow);
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
            min-height: 3.25rem;
            border-radius: 16px;
            border: 1px solid var(--line);
            font-weight: 600;
            box-shadow: none;
            font-size: 1rem;
        }

        .stButton > button[kind="primary"],
        .stFormSubmitButton > button[kind="primary"] {
            background: linear-gradient(135deg, #2754ff, #1a73ff);
            color: white;
            border: none;
        }

        .stButton > button[kind="secondary"],
        .stDownloadButton > button {
            background: white;
            color: var(--ink);
        }

        .stTextArea textarea,
        .stTextInput input {
            border-radius: 18px !important;
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

        .leaseguard-panel-title {
            font-size: 1.6rem;
            font-weight: 700;
            color: var(--blue);
            margin-bottom: 0.9rem;
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
            background: #fbfdff;
            border: 1px solid var(--line);
            border-radius: 18px;
            color: var(--ink);
            line-height: 1.75;
        }

        .leaseguard-mode-icon {
            width: 3rem;
            height: 3rem;
            border-radius: 999px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-weight: 700;
            margin-bottom: 0.8rem;
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
            margin-top: 0.3rem;
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
