"""Streamlit front end for the LeaseGuard AI project."""

from __future__ import annotations

import copy
import hmac
import importlib.util
import json
import re
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

try:
    from frontend.audit_backend import run_live_pipeline, run_reader_pipeline, run_sample_pipeline
    from frontend.secrets_utils import get_secret
except ImportError:
    from audit_backend import run_live_pipeline, run_reader_pipeline, run_sample_pipeline
    from secrets_utils import get_secret


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHROMADB_PATH = PROJECT_ROOT / "agent" / "chromadb"
MOJIBAKE_REPLACEMENTS = {
    "â€™": "'",
    "â€˜": "'",
    "â€œ": '"',
    "â€": '"',
    "â€“": "-",
    "â€”": "-",
    "â€¦": "...",
    "Â·": " - ",
    "Â": "",
}

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

COMMON_LOCATIONS = [
    "Chicago, IL",
    "New York, NY",
    "Los Angeles, CA",
    "Houston, TX",
    "Phoenix, AZ",
    "Philadelphia, PA",
    "San Francisco, CA",
    "Seattle, WA",
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
    st.session_state.setdefault("audit_results", None)
    st.session_state.setdefault("audit_source", None)
    st.session_state.setdefault("reader_results", None)
    st.session_state.setdefault("reader_source", None)
    st.session_state.setdefault("pending_term", None)
    st.session_state.setdefault("last_term", None)
    st.session_state.setdefault("last_result", None)
    st.session_state.setdefault("pending_location", None)
    st.session_state.setdefault("last_location", None)
    st.session_state.setdefault("last_resources", None)
    st.session_state.setdefault("last_resources_raw", None)


def _load_sample_audit() -> None:
    results = copy.deepcopy(run_sample_pipeline())
    st.session_state["audit_results"] = results
    st.session_state["audit_source"] = "sample"
    st.session_state["reader_results"] = results
    st.session_state["reader_source"] = "sample"


def _severity_label(score: float | int | None) -> str:
    if score is None:
        return "Needs review"
    if score >= 7:
        return "High risk"
    if score >= 4:
        return "Medium risk"
    return "Low risk"


def _report_preview(report: str, max_lines: int = 4) -> str:
    lines = [line.rstrip() for line in report.splitlines() if line.strip()]
    return "\n".join(lines[:max_lines])


def _compact_explanation(text: str, sentence_limit: int = 2) -> str:
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return "No explanation available."
    pieces = re.split(r"(?<=[.!?])\s+", cleaned)
    trimmed = " ".join(piece for piece in pieces[:sentence_limit] if piece)
    shortened = trimmed or cleaned
    if len(shortened) > 240:
        shortened = shortened[:237].rsplit(" ", 1)[0] + "..."
    return shortened


def _severity_markup(score: float | int | None) -> str:
    if score is None:
        return ":gray[Needs review]"
    if score >= 7:
        return ":red[High risk]"
    if score >= 4:
        return ":orange[Medium risk]"
    return ":green[Low risk]"


def _store_reader_results(results: dict[str, Any], source: str) -> None:
    st.session_state["reader_results"] = copy.deepcopy(results)
    st.session_state["reader_source"] = source


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


def _get_reader_content() -> tuple[dict[str, Any], str]:
    current_results = st.session_state.get("reader_results")
    current_source = st.session_state.get("reader_source")
    if current_results and current_results.get("reader_sections"):
        return current_results, current_source or "reader"

    current_results = st.session_state.get("audit_results")
    current_source = st.session_state.get("audit_source")
    if current_results and (
        current_results.get("reader_sections")
        or current_results.get("cleaned_lease_text")
        or current_results.get("plain_language_translation")
    ):
        return current_results, current_source or "live"
    return {"reader_sections": []}, "empty"


def _render_audit_results(results: dict[str, Any], source: str | None) -> None:
    findings = results.get("findings", [])
    report = results.get("report", "")

    flagged_findings = [item for item in findings if item.get("label") != "fair"]
    fair_findings = [item for item in findings if item.get("label") == "fair"]

    if source == "sample":
        st.caption("Sample review")

    st.subheader("Flagged clauses", anchor=False)
    if flagged_findings:
        for finding in sorted(flagged_findings, key=lambda item: item.get("severity") or 0, reverse=True):
            severity = finding.get("severity")
            score_text = f"{severity:g}/10" if severity is not None else "Needs review"
            clause_name = finding.get("clause_name", "Unnamed clause")
            with st.container(border=True):
                label_col, name_col, score_col = st.columns([2, 7, 2])
                with label_col:
                    st.markdown(_severity_markup(severity))
                with name_col:
                    st.markdown(f"**{clause_name}**")
                with score_col:
                    st.markdown(f"**{score_text}**")
                st.write(_compact_explanation(finding.get("explanation", "")))
    else:
        st.info("No flagged clauses were available in this review.")

    if fair_findings:
        with st.expander("Fair or standard clauses", expanded=False):
            for finding in fair_findings:
                st.markdown(f"- {finding.get('clause_name', 'Unnamed clause')}")

    st.subheader("Negotiation and next steps", anchor=False)
    st.markdown(_report_preview(report))
    with st.expander("Read full recommendations"):
        st.markdown(report)
    st.download_button(
        "Download review summary",
        data=report,
        file_name="lease_audit_report.md",
        mime="text/markdown",
        use_container_width=True,
    )
    st.caption(
        "LeaseGuard provides informational support. Legal advice comes from a licensed "
        "attorney or housing advocate."
    )


def _render_reader_sections(results: dict[str, Any]) -> None:
    sections = results.get("reader_sections") or []
    if not sections:
        st.info("Load a lease to see the original text and plain-English version.")
        return

    for section in sections:
        with st.container(border=True):
            st.markdown(f"**{section.get('title', 'Lease section')}**")
            st.caption("Original text")
            st.write(section.get("original_text", ""))
            st.caption("Plain English")
            st.write(section.get("plain_english", ""))


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


def _render_lease_audit_page() -> None:
    capabilities = _get_capabilities()
    full_review_available = capabilities["full_live_audit"]["available"]
    primary_action_label = "Review lease"

    st.header("Lease Review", anchor=False)
    st.write(
        "Upload a lease to flag risky clauses, understand the biggest issues, and prepare for "
        "negotiation before you sign."
    )

    if full_review_available:
        st.subheader("Start a review", anchor=False)
        with st.form("audit_form"):
            st.caption("Location helps tailor the review to local renter protections.")
            form_cols = st.columns(2)
            with form_cols[0]:
                city = st.text_input("City", placeholder="e.g., Chicago", key="audit_city")
            with form_cols[1]:
                state = st.text_input("State", placeholder="e.g., IL", key="audit_state")

            uploaded_file = st.file_uploader(
                "Lease file",
                type=["pdf", "docx"],
                help="Upload a PDF or DOCX lease.",
            )
            run_analysis = st.form_submit_button(
                primary_action_label,
                type="primary",
                use_container_width=True,
            )

        if run_analysis:
            if not uploaded_file:
                st.warning("Add a PDF or DOCX lease to continue.")
            elif not city or not state:
                st.warning("Add city and state to tailor the review.")
            else:
                suffix = Path(uploaded_file.name).suffix
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                    temp_file.write(uploaded_file.getbuffer())
                    temp_path = temp_file.name

                with st.status("Reviewing lease...", expanded=True) as status:
                    progress_slot = st.empty()

                    def _on_stage(message: str) -> None:
                        progress_slot.write(message)

                    try:
                        results = run_live_pipeline(
                            file_path=temp_path,
                            city=city,
                            state=state,
                            on_stage=_on_stage,
                        )
                        st.session_state["audit_results"] = results
                        st.session_state["audit_source"] = "live"
                        _store_reader_results(results, "live")
                        status.update(label="Lease ready", state="complete")
                    except Exception:
                        status.update(label="Review unavailable", state="error")
                        st.error("LeaseGuard couldn't complete the lease review.")
                    finally:
                        Path(temp_path).unlink(missing_ok=True)
    else:
        st.warning("Full lease review is unavailable right now.")

    st.button(
        "Try sample lease",
        key="sample_review_button",
        use_container_width=False,
        on_click=_load_sample_audit,
        type="secondary",
    )

    if st.session_state.get("audit_results"):
        results = st.session_state["audit_results"]
        if results.get("findings"):
            _render_audit_results(
                results,
                st.session_state.get("audit_source"),
            )
        elif st.session_state.get("audit_source") == "sample":
            _render_audit_results(results, "sample")

def _render_term_page() -> None:
    st.header("Read the Lease", anchor=False)
    st.write(
        "Read the original lease text and a short plain-English version of each section."
    )

    with st.form("reader_upload_form"):
        uploaded_file = st.file_uploader(
            "Lease file",
            type=["pdf", "docx"],
            help="Upload a PDF or DOCX lease.",
            key="reader_upload",
        )
        load_reader = st.form_submit_button(
            "Open lease",
            type="primary",
            use_container_width=True,
        )

    if load_reader:
        if not uploaded_file:
            st.warning("Add a PDF or DOCX lease to continue.")
        else:
            suffix = Path(uploaded_file.name).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
                temp_file.write(uploaded_file.getbuffer())
                temp_path = temp_file.name

            with st.status("Opening lease...", expanded=True) as status:
                progress_slot = st.empty()

                def _on_stage(message: str) -> None:
                    progress_slot.write(message)

                try:
                    results = run_reader_pipeline(
                        file_path=temp_path,
                        on_stage=_on_stage,
                    )
                    _store_reader_results(results, "upload")
                    status.update(label="Lease ready", state="complete")
                except Exception:
                    status.update(label="Lease unavailable", state="error")
                    st.error("LeaseGuard couldn't open that file.")
                finally:
                    Path(temp_path).unlink(missing_ok=True)

    reader_results, _reader_source = _get_reader_content()
    st.subheader("Original text and plain English", anchor=False)
    _render_reader_sections(reader_results)

    st.subheader("Terms explained", anchor=False)
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
        st.subheader(st.session_state["last_term"], anchor=False)
        st.markdown(st.session_state["last_result"])


def _render_resource_page() -> None:
    st.header("Get Local Help", anchor=False)
    st.write("Find tenant organizations, legal aid, and housing support based on your location.")

    with st.form("resource_lookup", clear_on_submit=True):
        typed_location = st.text_input(
            "Enter a city and state",
            placeholder="Example: Chicago, IL",
        )
        submitted = st.form_submit_button("Find support")

    st.subheader("Try a city", anchor=False)
    chip_cols = st.columns(4)
    for index, location in enumerate(COMMON_LOCATIONS):
        with chip_cols[index % 4]:
            if st.button(location, key=f"location_{index}", use_container_width=True):
                st.session_state["pending_location"] = location

    active_location = st.session_state.get("pending_location") or (
        typed_location if submitted else None
    )
    st.session_state["pending_location"] = None

    if active_location:
        display_location, resources, source = _get_resource_result(active_location)
        st.session_state["last_location"] = display_location
        st.session_state["last_resources"] = resources
        st.session_state["last_resources_raw"] = source

    if st.session_state.get("last_location") and st.session_state.get("last_resources"):
        st.subheader(f"Support near {st.session_state['last_location']}", anchor=False)
        for resource in st.session_state["last_resources"]:
            with st.container(border=True):
                st.markdown(f"**{resource.get('name', 'Unknown organization')}**")
                if resource.get("type"):
                    st.caption(resource["type"])
                st.write(resource.get("description", ""))
                if resource.get("url"):
                    st.markdown(f"[Visit website]({resource['url']})")
                if resource.get("phone"):
                    st.write(f"Phone: {resource['phone']}")


def _apply_styles() -> None:
    st.set_page_config(
        page_title="LeaseGuard AI",
        page_icon=":house:",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.markdown(
        """
        <style>
        :root {
            --line: #d9e1e7;
        }

        .stApp {
            background: #f7f8fa;
        }

        .block-container {
            max-width: 960px;
            padding-top: 1.5rem;
            padding-bottom: 3rem;
        }

        [data-testid="stSidebar"] {
            background: #f3f5f7;
            border-right: 1px solid var(--line);
        }

        div[data-testid="stExpander"] {
            border: 1px solid var(--line);
            border-radius: 12px;
            background: white;
        }

        div[data-testid="stFileUploader"] section {
            border-radius: 12px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar() -> str:
    with st.sidebar:
        st.title("LeaseGuard AI", anchor=False)
        st.caption("Understand what you're agreeing to before you sign.")
        page = st.radio(
            "Navigate",
            ["Lease Review", "Read the Lease", "Get Local Help"],
            label_visibility="collapsed",
        )
    return page


def main() -> None:
    _apply_styles()
    _init_state()
    if not _render_password_gate():
        st.stop()
    page = _render_sidebar()

    if page == "Lease Review":
        _render_lease_audit_page()
    elif page == "Read the Lease":
        _render_term_page()
    else:
        _render_resource_page()


if __name__ == "__main__":
    main()
