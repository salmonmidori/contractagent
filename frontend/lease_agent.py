"""
Lease Analysis Agent — extracted from agent/agent.ipynb
=================================================================
Provides a single entry point `run_pipeline(file_path, city, state)`
that runs the 4-stage lease analysis and returns structured results.

This module reuses the pre-built ChromaDB indices and RAG data from
the project's agent/ and rag_data/ directories.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup

# --- LangChain ---
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool

# --- LlamaIndex RAG ---
from llama_index.core import (
    SimpleDirectoryReader,
    StorageContext,
    VectorStoreIndex,
    Settings,
)
from llama_index.core.node_parser import SentenceSplitter
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI as LlamaIndexOpenAI
from llama_index.vector_stores.chroma import ChromaVectorStore

# --- ChromaDB ---
import chromadb

# ===================================================================
# Path resolution
# ===================================================================

_PROJECT_ROOT = Path(__file__).parent.parent
_DIR_AGENT = _PROJECT_ROOT / "agent"
_DIR_RAG_DATA = _PROJECT_ROOT / "rag_data"
_DIR_CHROMADB = _DIR_AGENT / "chromadb"
_CACHE_FILE = _DIR_AGENT / "agent_cache.json"


# ===================================================================
# Secrets loading
# ===================================================================

def _load_secrets() -> dict:
    """Load key=value pairs from secrets.txt into a dict."""
    secrets_path = _PROJECT_ROOT / "secrets.txt"
    secrets = {}
    if secrets_path.exists():
        for line in secrets_path.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, val = line.split("=", 1)
                secrets[key.strip()] = val.strip()
    return secrets


def _ensure_api_keys():
    """Push API keys from secrets.txt into os.environ if not already set."""
    secrets = _load_secrets()
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
        if key not in os.environ and key in secrets:
            os.environ[key] = secrets[key]


# ===================================================================
# Model & RAG initialisation (lazy, runs once)
# ===================================================================

_initialised = False
_llm = None
_llm_with_tools = None
_query_engine_gold = None
_query_engine_other = None
_query_engine_info = None
_tools = []
_tool_map = {}

# Model configuration — matches agent.ipynb
OPENAI_MODEL_ID = "gpt-4o-mini"


def _init_models_and_rag():
    """One-time setup of LLM, embeddings, and RAG query engines."""
    global _initialised, _llm, _llm_with_tools
    global _query_engine_gold, _query_engine_other, _query_engine_info
    global _tools, _tool_map

    if _initialised:
        return

    _ensure_api_keys()

    # LLM
    _llm = ChatOpenAI(model=OPENAI_MODEL_ID, temperature=0)

    # LlamaIndex settings
    Settings.llm = LlamaIndexOpenAI(model=OPENAI_MODEL_ID)
    Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small")

    # Load existing ChromaDB collections (built by the notebook)
    chroma_client = chromadb.PersistentClient(path=str(_DIR_CHROMADB))

    def _load_index(collection_name):
        collection = chroma_client.get_or_create_collection(collection_name)
        vector_store = ChromaVectorStore(chroma_collection=collection)
        return VectorStoreIndex.from_vector_store(
            vector_store, embed_model=Settings.embed_model
        )

    _query_engine_gold = _load_index("gold_standard_leases").as_query_engine()
    _query_engine_other = _load_index("other_leases").as_query_engine()
    _query_engine_info = _load_index("lease_info").as_query_engine()

    # Register tools (defined below at module level)
    _tools = [
        retrieve_gold_standard_clauses,
        retrieve_other_lease_examples,
        retrieve_lease_info,
        search_legal_web,
    ]
    _tool_map = {t.name: t for t in _tools}
    _llm_with_tools = _llm.bind_tools(_tools)
    _initialised = True


# ===================================================================
# Agent tools
# ===================================================================

@tool
def retrieve_gold_standard_clauses(query: str) -> str:
    """Retrieve examples of fair, standard lease language from gold standard
    lease templates for comparison."""
    return str(_query_engine_gold.query(query))


@tool
def retrieve_other_lease_examples(query: str) -> str:
    """Retrieve examples from real-world lease agreements for comparison."""
    return str(_query_engine_other.query(query))


@tool
def retrieve_lease_info(query: str) -> str:
    """Retrieve educational information about lease red flags, illegal clauses,
    and best practices for tenants."""
    return str(_query_engine_info.query(query))


ALLOWED_DOMAINS = [
    "nolo.com", "law.cornell.edu", "findlaw.com", "justia.com",
    "hud.gov", "usa.gov", "tenant.net", "landlordology.com", "avail.co",
]

DOMAIN_URL_PATTERNS = {
    "nolo.com": [
        "https://www.nolo.com/legal-encyclopedia/tenants-rights-{state}.html",
        "https://www.nolo.com/legal-encyclopedia/{topic}.html",
    ],
    "findlaw.com": [
        "https://www.findlaw.com/state/{state}/landlord-tenant-law.html",
        "https://www.findlaw.com/realestate/landlord-tenant-law/{topic}.html",
    ],
    "justia.com": [
        "https://www.justia.com/real-estate/landlord-tenant/{topic}/",
    ],
    "law.cornell.edu": [
        "https://www.law.cornell.edu/wex/landlord-tenant_law",
    ],
    "hud.gov": [
        "https://www.hud.gov/topics/rental_assistance",
    ],
    "tenant.net": [
        "https://www.tenant.net/rights/{topic}/",
    ],
    "avail.co": [
        "https://www.avail.co/education/laws/{state}-landlord-tenant-law",
    ],
    "landlordology.com": [
        "https://www.landlordology.com/{state}/landlord-tenant-law/",
    ],
    "usa.gov": [
        "https://www.usa.gov/landlord-tenant-disputes",
    ],
}


def _fetch_page_text(url, max_chars=3000):
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text[:max_chars] if text.strip() else None
    except requests.RequestException:
        return None


# Module-level variable set per-run for the renter's state
_renter_state = "Illinois"


@tool
def search_legal_web(query: str, state: str = "") -> str:
    """Search reputable legal websites for state-specific tenant rights."""
    if not state:
        state = _renter_state
    state_slug = state.lower().replace(" ", "-")
    topic_slug = query.lower().replace(" ", "-")
    results = []
    for domain in ALLOWED_DOMAINS:
        patterns = DOMAIN_URL_PATTERNS.get(domain, [])
        for pattern in patterns:
            url = pattern.format(state=state_slug, topic=topic_slug)
            text = _fetch_page_text(url)
            if text:
                results.append(f"[{domain}] ({url})\n{text}")
                break
    if results:
        return "\n\n---\n\n".join(results)
    return f"No results found for '{query}' in {state}."


# ===================================================================
# Caching
# ===================================================================

def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        return json.loads(_CACHE_FILE.read_text())
    return {}


def _save_cache(cache: dict):
    _CACHE_FILE.write_text(json.dumps(cache, indent=2))


def _cache_key(stage_name: str, content_hash: str) -> str:
    key_str = f"{stage_name}:{content_hash}"
    return hashlib.sha256(key_str.encode()).hexdigest()[:16]


# ===================================================================
# LLM tool-calling loop
# ===================================================================

def _call_llm_with_tools(messages, max_tool_rounds=3, stage_name=None):
    cache = _load_cache()
    if stage_name:
        content_str = "|".join(m.content for m in messages if hasattr(m, "content"))
        content_hash = hashlib.sha256(content_str.encode()).hexdigest()[:32]
        ck = _cache_key(stage_name, content_hash)
        if ck in cache:
            return cache[ck]
    else:
        ck = None

    for _ in range(max_tool_rounds):
        response = _llm_with_tools.invoke(messages)
        messages.append(response)
        if not response.tool_calls:
            result = response.content
            break
        for tc in response.tool_calls:
            tool_fn = _tool_map[tc["name"]]
            tool_result = tool_fn.invoke(tc["args"])
            messages.append(ToolMessage(content=str(tool_result), tool_call_id=tc["id"]))
    else:
        response = _llm.invoke(messages)
        result = response.content

    if ck:
        cache[ck] = result
        _save_cache(cache)

    return result


# ===================================================================
# Lease ingestion
# ===================================================================

def ingest_user_lease(file_path: str) -> str:
    """Extract text from a lease PDF/DOCX using LlamaIndex."""
    documents = SimpleDirectoryReader(input_files=[file_path]).load_data()
    text = "\n\n".join([doc.text for doc in documents])
    return text


# ===================================================================
# Output parsing
# ===================================================================

def parse_clause_analysis(output_text: str) -> list[dict]:
    """Parse structured clause analysis from LLM output into a list of dicts."""
    boundary = re.compile(
        r'(?=(?:^|\n)'
        r'(?:'
        r'\d+[\.\)]\s'
        r'|#{2,}\s'
        r'|-\s*\*\*'
        r'|\*\*Clause'
        r'|Clause\s*[\d#]*\s*:'
        r'))',
        re.IGNORECASE | re.MULTILINE,
    )

    positions = [m.start() for m in boundary.finditer(output_text)]
    if not positions:
        return [_extract_fields(output_text)]

    sections = []
    for i, start in enumerate(positions):
        end = positions[i + 1] if i + 1 < len(positions) else len(output_text)
        section = output_text[start:end].strip()
        if section:
            sections.append(section)

    findings = []
    for section in sections:
        parsed = _extract_fields(section)
        if parsed["clause_name"] or parsed["label"]:
            findings.append(parsed)
    return findings


def _extract_fields(section_text: str) -> dict:
    lines = [ln.strip() for ln in section_text.splitlines() if ln.strip()]
    result = {
        "clause_name": None,
        "label": None,
        "severity": None,
        "explanation": None,
        "raw_output": section_text,
    }
    # Ordered most-specific first: "fair" is a substring of "unfair but legal",
    # so checking order matters to avoid false matches.
    valid_labels = [
        "illegal",
        "unfair but legal",
        "unclear or ambiguous",
        "unclear",
        "ambiguous",
        "outdated",
        "fair",
    ]
    for line in lines:
        low = line.lower()
        clean = re.sub(r'[\*#\-]+', '', line).strip()

        if result["clause_name"] is None:
            m = re.search(r'clause[^:]*:\s*(.+)', low)
            if m:
                result["clause_name"] = re.sub(r'[\*#]+', '', m.group(1)).strip().title()
                continue
            if re.match(r'^\d+[\.\)]', line) and result["clause_name"] is None:
                name = re.sub(r'^\d+[\.\)]\s*', '', clean)
                if name and "label" not in name.lower() and "severity" not in name.lower():
                    result["clause_name"] = name.strip(":").title()
                    continue

        if result["label"] is None and "label" in low and ":" in line:
            val = line.split(":", 1)[1].strip().strip("*").lower()
            for lbl in valid_labels:
                if lbl in val:
                    result["label"] = lbl
                    break
            if result["label"] is None and val:
                result["label"] = val

        if result["severity"] is None and "severity" in low and ":" in line:
            nums = re.findall(r'[\d]+(?:\.[\d]+)?', line.split(":", 1)[1])
            if nums:
                try:
                    result["severity"] = float(nums[0])
                except ValueError:
                    pass

        if result["explanation"] is None and "explanation" in low and ":" in line:
            result["explanation"] = line.split(":", 1)[1].strip().strip("*")

    return result


# ===================================================================
# Prompt templates
# ===================================================================

# Stage 1: finalized
PROMPT_DEFINE_STANDARDS = """
You are a tenant rights legal expert. Your job is to establish the legal and contractual
standards that apply to the lease being reviewed.

Using the tools available to you:
1. Retrieve examples of fair, gold-standard lease language for comparison.
2. Search for tenant rights laws and landlord obligations applicable to the renter's jurisdiction.

Then produce a structured standards framework covering:
- Key statutes and local ordinances that apply (e.g. security deposit limits, notice requirements)
- Provisions that are legally required in leases in this jurisdiction
- Provisions that are prohibited or unenforceable by law
- What fair, balanced language looks like for common clause types (rent, deposits, entry, repairs, termination)
- Red flag language patterns that commonly disadvantage tenants

ACCURACY RULES — follow these strictly:
- Only assert something is "illegal" or "required by law" if you can cite a specific statute or ordinance.
- Distinguish carefully between: (a) rules that apply IF a landlord does something (e.g. IF a security
  deposit is collected, THEN it must be held in a specific account) vs. (b) things a landlord is
  required to do regardless. Do NOT conflate these.
- If you are unsure about a specific legal requirement, say so rather than guessing.

Be specific to the renter's city and state. This framework will be used to evaluate every clause
in the lease, so accuracy is more important than comprehensiveness.
""".strip()

# Stage 2: winner from prompt lab (v3_expert)
PROMPT_ANALYZE_CLAUSES = """
You are a tenant rights attorney with 20 years of experience reviewing residential leases.
A renter has hired you to protect their interests. Your professional reputation depends on catching every problem.

Audit every single clause in this lease. For each clause:
1. Consider what the standards framework says about this type of clause.
2. Ask: does this language protect the tenant, expose them to risk, or is it neutral?
3. Assign a label and severity, then explain your reasoning.

Use exactly this format for each clause:

**Clause**: [clause name or short description]
**Label**: [illegal / unfair but legal / unclear or ambiguous / outdated / fair]
**Severity**: [integer 1-10, where 10 is most harmful to the tenant]
**Explanation**: [1-2 sentences citing specific laws or standard benchmarks]

Label definitions:
- illegal: violates applicable housing law; unenforceable — ONLY use this if you can cite a specific law
- unfair but legal: legal but significantly disadvantages the tenant
- unclear or ambiguous: vague or exploitable language
- outdated: no longer reflects current law or practice
- fair: balanced and reasonable

ACCURACY RULES — follow these strictly:
- Do NOT label a clause "illegal" unless you can name the specific statute or ordinance it violates.
- Pay close attention to the DIRECTION of legal obligations. Laws often say "IF a landlord does X,
  THEN they must do Y." That is NOT the same as requiring landlords to do X in the first place.
- When in doubt about legality, use "unclear or ambiguous" rather than "illegal".

CONCRETE EXAMPLES — these are common mistakes you MUST NOT make:
1. "NO Security Deposit" or a lease that waives the security deposit → label this FAIR or at worst
   "unfair but legal" (tenant loses deposit protection). It is NOT illegal. Chicago RLTO governs how
   deposits must be handled IF a landlord collects one; it does not require landlords to collect a
   deposit. A lease without any deposit cannot violate deposit-handling rules.
2. A clause saying the landlord is not liable for tenant's personal property → "unfair but legal",
   NOT illegal. Tenants should carry renter's insurance, but this clause does not violate a statute.
3. A clause requiring tenant to pay landlord's attorney fees → check jurisdiction; in Illinois this
   is generally unenforceable but courts vary — label "unclear or ambiguous", NOT definitively illegal
   unless you can cite the specific statute.

Be exhaustive. Missing a problematic clause is a professional failure.
""".strip()

# Stage 3: finalized
PROMPT_PRIORITIZE_FINDINGS = """
You are a tenant advocate. Given the clause analysis results below, rank all non-fair findings
by how urgently the tenant should act on them.

Use this tier system:
- CRITICAL: illegal clauses or severity 8-10 (potential legal violation or major financial risk)
- HIGH: unfair but legal clauses, severity 5-7 (significantly disadvantages tenant)
- MEDIUM: unclear or ambiguous clauses, severity 3-4 (negotiate for clarity)
- LOW: minor issues, severity 1-2 (worth noting but not urgent)

For each finding output:
  Tier | Clause Name | Why it's ranked here | Negotiable? (yes/no/maybe)

List CRITICAL items first. Skip clauses labeled "fair".
""".strip()

# Stage 4: winner from prompt lab (v3_expert)
PROMPT_GENERATE_REPORT = """
You are a tenant rights advocate writing a report for a renter who is not a legal expert.
Your job is to be their champion — clear, empowering, and actionable.

Generate a complete Improvement Decision Report with these sections:

1. **Executive Summary**: 2-3 sentences. Give the tenant a clear bottom line —
   is this lease safe to sign, risky, or problematic?
2. **Key Facts**: Bullet list of material terms (rent, deposit, lease term, notice periods, etc.)
3. **Risk Assessment**: All non-fair clauses grouped by tier (CRITICAL → HIGH → MEDIUM → LOW).
   For each: clause name, label, severity score, one-sentence risk description.
4. **Prioritized Improvements**: For each CRITICAL and HIGH item:
   - The problem in plain language
   - Why it matters financially or legally
   - Exact negotiation language the tenant can use
5. **Educational Notes**: Define legal terms used (joint and several liability, holdover tenant,
   habitability, abatement, etc.) in plain language.
6. **Advocacy Resources**: Note any local tenant rights organizations relevant to the tenant's location.
7. **Citations**: All laws, ordinances, and standards referenced.

Write at an 8th-grade reading level. Tone: empowering, not overwhelming.

ACCURACY RULES:
- Only cite a law as violated if the clause analysis explicitly identified it as illegal with a citation.
- Do not introduce new legal claims that weren't in the clause analysis.
""".strip()


# ===================================================================
# 4-stage pipeline
# ===================================================================

def _stage_define_standards(contract_text: str, city: str, state: str) -> str:
    messages = [
        SystemMessage(content=PROMPT_DEFINE_STANDARDS),
        HumanMessage(content=(
            f"Renter's Location: {city}, {state}\n\n"
            f"Lease Contract to Review:\n{contract_text[:8000]}\n\n"
            "Using the tools available to you, retrieve relevant legal standards "
            "and gold standard lease language for this jurisdiction. Then establish "
            "the baseline standards against which this lease will be evaluated."
        )),
    ]
    return _call_llm_with_tools(messages, stage_name="define_standards")


def _stage_analyze_clauses(contract_text: str, standards: str, city: str, state: str):
    messages = [
        SystemMessage(content=PROMPT_ANALYZE_CLAUSES),
        HumanMessage(content=(
            f"Renter's Location: {city}, {state}\n\n"
            f"Standards Framework:\n{standards}\n\n"
            f"Lease Contract:\n{contract_text}\n\n"
            "Analyze each clause in this lease. For each clause, provide:\n"
            "- **Clause**: name/description\n"
            "- **Label**: illegal / unfair but legal / unclear or ambiguous / outdated / fair\n"
            "- **Severity**: score from 1 (minor) to 10 (critical)\n"
            "- **Explanation**: why this clause received this label\n\n"
            "Use the tools to verify against legal standards and gold standard language."
        )),
    ]
    raw_analysis = _call_llm_with_tools(messages, stage_name="analyze_clauses")
    findings = parse_clause_analysis(raw_analysis)
    return findings, raw_analysis


def _stage_prioritize(findings, raw_analysis: str, standards: str, city: str, state: str) -> str:
    messages = [
        SystemMessage(content=PROMPT_PRIORITIZE_FINDINGS),
        HumanMessage(content=(
            f"Renter's Location: {city}, {state}\n\n"
            f"Standards Framework:\n{standards}\n\n"
            f"Clause Analysis Results:\n{raw_analysis}\n\n"
            "Rank the findings above by priority. Consider:\n"
            "- Legal risk (illegal clauses first)\n"
            "- Financial impact on the renter\n"
            "- Likelihood of successful negotiation\n"
            "- Long-term consequences\n"
            "Provide a numbered priority list with justification for the ranking."
        )),
    ]
    return _call_llm_with_tools(messages, stage_name="prioritize_findings")


def _stage_generate_report(
    contract_text: str, standards: str, raw_analysis: str,
    prioritized: str, city: str, state: str,
) -> str:
    messages = [
        SystemMessage(content=PROMPT_GENERATE_REPORT),
        HumanMessage(content=(
            f"Renter's Location: {city}, {state}\n\n"
            f"Standards Framework:\n{standards}\n\n"
            f"Clause Analysis:\n{raw_analysis}\n\n"
            f"Prioritized Findings:\n{prioritized}\n\n"
            "Generate a complete Improvement Decision Report. Include:\n"
            "1. Executive Summary of the lease review\n"
            "2. Facts: Key terms and conditions found\n"
            "3. Risk Assessment: Legal and financial risks identified\n"
            "4. Prioritized Improvements: Ranked list of clauses to negotiate\n"
            "5. Negotiation Tips: Actionable advice for each priority item\n"
            "6. Educational Notes: Explain key legal concepts\n"
            "7. Advocacy Resources: Use the search tool to find local resources\n"
            "8. Citations: Specific laws, ordinances, and standards referenced\n"
        )),
    ]
    return _call_llm_with_tools(messages, stage_name="generate_report")


def run_pipeline(
    file_path: str,
    city: str = "Chicago",
    state: str = "Illinois",
    on_stage: Optional[Callable[[str], None]] = None,
) -> dict:
    """Run the full 4-stage lease analysis pipeline.

    Args:
        file_path: Path to the uploaded lease file (PDF or DOCX).
        city: Renter's city for jurisdiction-aware analysis.
        state: Renter's state for jurisdiction-aware analysis.
        on_stage: Optional callback called with a status message at each stage.

    Returns:
        dict with keys: report, findings, standards, prioritized, raw_analysis
    """
    global _renter_state
    _renter_state = state

    _init_models_and_rag()

    def _status(msg):
        if on_stage:
            on_stage(msg)

    _status("Extracting text from lease...")
    contract_text = ingest_user_lease(file_path)

    _status("Stage 1/4: Defining legal standards for your jurisdiction...")
    standards = _stage_define_standards(contract_text, city, state)

    _status("Stage 2/4: Analyzing lease clauses...")
    findings, raw_analysis = _stage_analyze_clauses(contract_text, standards, city, state)

    _status("Stage 3/4: Prioritizing findings...")
    prioritized = _stage_prioritize(findings, raw_analysis, standards, city, state)

    _status("Stage 4/4: Generating improvement report...")
    report = _stage_generate_report(contract_text, standards, raw_analysis, prioritized, city, state)

    return {
        "report": report,
        "findings": findings,
        "standards": standards,
        "prioritized": prioritized,
        "raw_analysis": raw_analysis,
    }
