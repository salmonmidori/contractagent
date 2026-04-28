"""
LeaseGuard AI — AI-Driven Renter's Advocacy Platform (MVP)
=====================================================
Streamlit front-end for BUSN30135 Final Project.

Features:
    1. Automated Lease Audit   – Upload & mock-analysis dashboard
    2. Educational Foundation   – LLM-powered lease-term explainer
    3. Advocacy Bridge          – Location-based renter resource finder

Run:
    streamlit run frontend/streamlit_app.py
"""

import streamlit as st
import json
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# LLM helper – uses OpenAI via the course secrets.txt
# ---------------------------------------------------------------------------

def _load_api_key() -> str | None:
    """Read OPENAI_API_KEY from the project's secrets.txt."""
    secrets_path = Path(__file__).parent.parent / "secrets.txt"
    if not secrets_path.exists():
        return None
    for line in secrets_path.read_text().splitlines():
        line = line.strip()
        if line.startswith("OPENAI_API_KEY"):
            return line.split("=", 1)[1].strip()
    return None


def _call_llm(system_prompt: str, user_prompt: str) -> str:
    """Thin wrapper around the OpenAI ChatCompletion API."""
    try:
        from openai import OpenAI
    except ImportError:
        return "⚠️ `openai` package not installed. Run `pip install openai`."

    api_key = _load_api_key()
    if not api_key:
        return "⚠️ No OPENAI_API_KEY found in secrets.txt."

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=1024,
    )
    return response.choices[0].message.content


# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="LeaseGuard AI",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Global styles
# ---------------------------------------------------------------------------

st.markdown(
    """
    <style>
    /* Clean, trustworthy aesthetic */
    .block-container { max-width: 960px; padding-top: 2rem; }
    h1, h2, h3 { font-family: 'Inter', 'Segoe UI', sans-serif; }

    /* Score gauge colours */
    .score-high   { color: #1b7a3d; }
    .score-medium { color: #c27a00; }
    .score-low    { color: #c0392b; }

    /* Chip buttons — prevent awkward word wrapping */
    .stButton > button {
        white-space: nowrap;
        font-size: 0.85rem;
        padding: 0.35rem 0.6rem;
    }

    /* Card container */
    .card {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 1.25rem 1.5rem;
        margin-bottom: 1rem;
        border-left: 4px solid #4a90d9;
    }
    .card h4 { margin-top: 0; }

    /* Resource card */
    .resource-card {
        background-color: #f8f9fa;
        border-radius: 10px;
        padding: 1.25rem 1.5rem;
        margin-bottom: 1rem;
        border-left: 4px solid #27ae60;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image(
        "https://img.icons8.com/fluency/96/home.png",
        width=56,
    )
    st.title("LeaseGuard AI")
    st.caption("AI-powered renter advocacy")
    st.divider()

    page = st.radio(
        "Navigate",
        ["Lease Audit", "Learn Lease Terms", "Find Local Help"],
        label_visibility="collapsed",
    )

    st.divider()
    st.markdown(
        "<small>BUSN 30135 — Final Project<br>"
        "Amy · Brian · David · Fay · Graham</small>",
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# PAGE 1 — Automated Lease Audit  (UI / mock only)
# ═══════════════════════════════════════════════════════════════════════════

if page == "Lease Audit":
    st.header("Automated Lease Audit")
    st.markdown(
        "Upload your lease and our AI will flag predatory clauses, "
        "prioritize risks, and generate a full improvement report."
    )

    # --- Location inputs ---
    loc_col1, loc_col2 = st.columns(2)
    with loc_col1:
        audit_city = st.text_input(
            "City", placeholder="e.g., Chicago", key="audit_city"
        )
    with loc_col2:
        audit_state = st.text_input(
            "State", placeholder="e.g., Illinois", key="audit_state"
        )

    uploaded_file = st.file_uploader(
        "Upload your lease (PDF or DOCX)",
        type=["pdf", "docx"],
        help="Your file is processed locally and never stored.",
    )

    # Initialise session state for audit results
    if "audit_results" not in st.session_state:
        st.session_state["audit_results"] = None

    if uploaded_file is not None and audit_city and audit_state:
        # Write uploaded file to a temp path so LlamaIndex can read it
        suffix = Path(uploaded_file.name).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.getbuffer())
            tmp_path = tmp.name

        # Run the 4-stage pipeline with live status updates
        with st.status("Analyzing your lease...", expanded=True) as status:
            status_placeholder = st.empty()

            def _on_stage(msg):
                status_placeholder.write(msg)

            try:
                from frontend.lease_agent import run_pipeline
            except ImportError:
                from lease_agent import run_pipeline

            results = run_pipeline(
                file_path=tmp_path,
                city=audit_city,
                state=audit_state,
                on_stage=_on_stage,
            )
            status.update(label="Analysis complete!", state="complete")

        st.session_state["audit_results"] = results

        # Clean up temp file
        Path(tmp_path).unlink(missing_ok=True)

    elif uploaded_file is not None and (not audit_city or not audit_state):
        st.warning("Please enter your city and state above to begin analysis.")

    # --- Display results ---
    if st.session_state.get("audit_results"):
        results = st.session_state["audit_results"]
        findings = results["findings"]
        report = results["report"]

        st.divider()

        # --- Top row: summary metrics + clause findings ---
        col_score, col_summary = st.columns([1, 2])

        with col_score:
            # Count problematic clauses (non-fair)
            problematic = [
                f for f in findings
                if f.get("label") and f["label"] != "fair"
            ]
            st.markdown("#### Audit Summary")
            st.metric("Total Clauses Analysed", len(findings))
            st.metric("Problematic Clauses", len(problematic))

            # Severity breakdown
            high_sev = [f for f in findings if (f.get("severity") or 0) >= 7]
            med_sev = [f for f in findings if 4 <= (f.get("severity") or 0) < 7]
            low_sev = [f for f in findings if 0 < (f.get("severity") or 0) < 4]
            if high_sev or med_sev or low_sev:
                st.markdown(
                    f"🔴 High severity: **{len(high_sev)}**  \n"
                    f"🟡 Medium severity: **{len(med_sev)}**  \n"
                    f"🟢 Low severity: **{len(low_sev)}**"
                )

        with col_summary:
            st.markdown("#### Clause Findings")
            if findings:
                for f in sorted(findings, key=lambda f: f.get("severity") or 0, reverse=True):
                    sev = f.get("severity") or 0
                    if sev >= 7:
                        icon = "🔴"
                        sev_label = "High"
                    elif sev >= 4:
                        icon = "🟡"
                        sev_label = "Medium"
                    else:
                        icon = "🟢"
                        sev_label = "Low"

                    name = f.get("clause_name") or "Unnamed Clause"
                    label = f.get("label") or "unknown"
                    explanation = f.get("explanation") or f.get("raw_output", "")

                    with st.expander(
                        f"{icon} **{sev_label}** ({sev}/10) — {name} [{label}]"
                    ):
                        st.write(explanation)
            else:
                st.info("No individual clause findings were parsed.")

        # --- Full report ---
        st.divider()
        st.markdown("#### Improvement Decision Report")
        st.markdown(report)

        # --- Download button ---
        st.download_button(
            label="Download Report (.md)",
            data=report,
            file_name="lease_audit_report.md",
            mime="text/markdown",
        )

        st.divider()
        st.caption(
            "⚠️ This analysis is AI-generated and does not constitute legal advice. "
            "Consult a qualified attorney for legal guidance."
        )


# ═══════════════════════════════════════════════════════════════════════════
# PAGE 2 — Educational Foundation  (functional LLM-powered)
# ═══════════════════════════════════════════════════════════════════════════

elif page == "Learn Lease Terms":
    st.header("Lease Term Explainer")
    st.markdown(
        "Look up any lease or rental term and get a plain-English explanation "
        "with a focus on **financial implications** for tenants."
    )

    # Quick-pick chips
    common_terms = [
        "Joint & Several Liability",
        "Abatement",
        "Holdover Tenancy",
        "Security Deposit Interest",
        "Quiet Enjoyment",
        "Subletting vs. Assignment",
        "Constructive Eviction",
        "Lease Guaranty",
    ]

    # Initialise session state for this page
    if "pending_term" not in st.session_state:
        st.session_state["pending_term"] = None
    if "last_result" not in st.session_state:
        st.session_state["last_result"] = None
    if "last_term" not in st.session_state:
        st.session_state["last_term"] = None

    def _on_chip_click(term: str):
        st.session_state["pending_term"] = term

    st.markdown("**Common terms:**")
    chip_cols = st.columns(4)
    for i, term in enumerate(common_terms):
        with chip_cols[i % 4]:
            st.button(
                term,
                key=f"chip_{i}",
                use_container_width=True,
                on_click=_on_chip_click,
                args=(term,),
            )

    # Use a form so Enter submits, then we can clear the input
    with st.form("term_search_form", clear_on_submit=True):
        search_term = st.text_input(
            "Or type any lease term:",
            placeholder="e.g., escalation clause, right of first refusal...",
        )
        submitted = st.form_submit_button("Search")

    # A chip click takes priority; otherwise use the submitted text input
    active_term = st.session_state["pending_term"] or (search_term if submitted else None)
    # Clear the pending chip so it doesn't re-fire
    st.session_state["pending_term"] = None

    if active_term:
        system_prompt = (
            "You are a tenant-rights educator. When given a legal lease term, "
            "respond with EXACTLY this markdown structure — no extra sections:\n\n"
            "CORRECTED_TERM: <the correct spelling of the term>\n\n"
            "## Definition\nA 2-3 sentence plain-English definition.\n\n"
            "## How It Works in a Lease\nA short paragraph explaining how this "
            "clause typically appears.\n\n"
            "## Financial Implications for Tenants\nBullet points describing "
            "the monetary impact, risks, and what to watch for.\n\n"
            "## Negotiation Tip\nOne concrete, actionable suggestion for "
            "the tenant.\n\n"
            "IMPORTANT: The FIRST line of your response MUST be "
            "CORRECTED_TERM: followed by the correctly spelled term name. "
            "If the user's input has a typo, fix it. If it's already correct, "
            "repeat it as-is."
        )

        with st.spinner(f"Looking up **{active_term}**..."):
            result = _call_llm(system_prompt, f"Explain: {active_term}")

        # Extract corrected term from the first line
        display_term = active_term
        lines = result.split("\n", 1)
        if lines[0].startswith("CORRECTED_TERM:"):
            display_term = lines[0].replace("CORRECTED_TERM:", "").strip()
            result = lines[1].lstrip("\n") if len(lines) > 1 else ""

        st.session_state["last_result"] = result
        st.session_state["last_term"] = display_term

    # Display results (persists across reruns until a new search)
    if st.session_state.get("last_result"):
        st.divider()
        st.markdown(f"## {st.session_state['last_term']}")
        # Downsize LLM section headers from ## to ###
        formatted = st.session_state["last_result"].replace("## ", "### ")
        st.markdown(formatted)


# ═══════════════════════════════════════════════════════════════════════════
# PAGE 3 — Advocacy Bridge  (functional location-based search)
# ═══════════════════════════════════════════════════════════════════════════

elif page == "Find Local Help":
    st.header("Find Renter Advocacy Resources")
    st.markdown(
        "Enter your city and state to discover local non-profits, legal aid "
        "organizations, and tenant hotlines in your area."
    )

    # Quick-pick location chips
    common_locations = [
        "Chicago, IL",
        "New York, NY",
        "Los Angeles, CA",
        "Houston, TX",
        "Phoenix, AZ",
        "Philadelphia, PA",
        "San Francisco, CA",
        "Seattle, WA",
    ]

    # Initialise session state for this page
    if "pending_location" not in st.session_state:
        st.session_state["pending_location"] = None
    if "last_resources" not in st.session_state:
        st.session_state["last_resources"] = None
    if "last_resources_raw" not in st.session_state:
        st.session_state["last_resources_raw"] = None
    if "last_location" not in st.session_state:
        st.session_state["last_location"] = None

    def _on_location_click(loc: str):
        st.session_state["pending_location"] = loc

    st.markdown("**Popular cities:**")
    loc_cols = st.columns(4)
    for i, loc in enumerate(common_locations):
        with loc_cols[i % 4]:
            st.button(
                loc,
                key=f"loc_chip_{i}",
                use_container_width=True,
                on_click=_on_location_click,
                args=(loc,),
            )

    # Use a form so Enter submits, then clear the input
    with st.form("location_search_form", clear_on_submit=True):
        location = st.text_input(
            "Or type your location:",
            placeholder="e.g., Jupiter, FL",
        )
        loc_submitted = st.form_submit_button("Search")

    # A chip click takes priority; otherwise use the submitted text input
    active_location = st.session_state["pending_location"] or (location if loc_submitted else None)
    # Clear the pending chip so it doesn't re-fire
    st.session_state["pending_location"] = None

    if active_location:
        system_prompt = (
            "You are a tenant-rights resource specialist. Given a US location, "
            "return EXACTLY 3-5 real, currently operating renter advocacy "
            "organizations or legal aid providers near that location.\n\n"
            "The FIRST line of your response MUST be:\n"
            "CORRECTED_LOCATION: City, ST\n"
            "where City is the correctly spelled city name and ST is the "
            "two-letter state abbreviation. If the user's input has a typo, "
            "fix it. If it's already correct, repeat it as-is.\n\n"
            "After that first line, return the resources as a JSON array "
            "(no markdown fences, just raw JSON):\n"
            "[\n"
            "  {\n"
            '    "name": "Organization Name",\n'
            '    "type": "Legal Aid | Non-Profit | Government | Hotline",\n'
            '    "description": "2-3 sentence summary of services.",\n'
            '    "url": "https://...",\n'
            '    "phone": "xxx-xxx-xxxx or null"\n'
            "  }\n"
            "]\n\n"
            "Only include organizations you are confident are real. "
            "If you are unsure about a URL, set url to null."
        )

        with st.spinner(f"Searching resources near **{active_location}**..."):
            raw = _call_llm(system_prompt, f"Location: {active_location}")

        # Extract corrected location from the first line
        display_location = active_location
        raw_body = raw
        first_line, _, rest = raw.partition("\n")
        if first_line.startswith("CORRECTED_LOCATION:"):
            display_location = first_line.replace("CORRECTED_LOCATION:", "").strip()
            raw_body = rest.lstrip("\n")

        # Parse the LLM JSON response
        resources = None
        try:
            cleaned = raw_body.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[:-1])
            resources = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            resources = None

        st.session_state["last_resources"] = resources
        st.session_state["last_resources_raw"] = raw_body
        st.session_state["last_location"] = display_location

    # Display results (persists across reruns until a new search)
    if st.session_state.get("last_location"):
        st.divider()
        st.markdown(f"## Resources near {st.session_state['last_location']}")

        resources = st.session_state["last_resources"]
        if resources and isinstance(resources, list):
            for res in resources:
                name = res.get("name", "Unknown")
                org_type = res.get("type", "")
                desc = res.get("description", "")
                url = res.get("url")
                phone = res.get("phone")

                link_md = f"[Visit website]({url})" if url else "*No website available*"
                phone_md = f"📞 {phone}" if phone else ""

                st.markdown(
                    f'<div class="resource-card">'
                    f"<h4>{name}</h4>"
                    f"<p><strong>{org_type}</strong></p>"
                    f"<p>{desc}</p>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                cols = st.columns([2, 1])
                with cols[0]:
                    st.markdown(link_md)
                with cols[1]:
                    if phone_md:
                        st.markdown(phone_md)
        else:
            st.warning("Could not parse structured results. Showing raw response:")
            st.markdown(st.session_state["last_resources_raw"])

        st.divider()
        st.caption(
            "⚠️ Resources are surfaced by AI and may not be exhaustive. "
            "Verify contact details before relying on them. This is not legal advice."
        )
