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
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

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

try:
    from frontend.secrets_utils import get_secret, prepare_network_env
except ImportError:
    from secrets_utils import get_secret, prepare_network_env

# ===================================================================
# Path resolution
# ===================================================================

_PROJECT_ROOT = Path(__file__).parent.parent
_DIR_AGENT = _PROJECT_ROOT / "agent"
_DIR_RAG_DATA = _PROJECT_ROOT / "rag_data"
_DIR_CHROMADB = _DIR_AGENT / "chromadb"
_RUNTIME_ROOT = Path(tempfile.gettempdir()) / "leaseguard_runtime"
_CACHE_FILE = _RUNTIME_ROOT / "agent_cache.json"


# ===================================================================
# Secrets loading
# ===================================================================


def _ensure_api_keys():
    """Push API keys from env, Streamlit secrets, or local secrets.txt into os.environ."""
    prepare_network_env()
    for key in ("OPENAI_API_KEY", "GEMINI_API_KEY"):
        if key in os.environ and os.environ[key].strip():
            continue

        secret_value = get_secret(key, project_root=_PROJECT_ROOT)
        if secret_value:
            os.environ[key] = secret_value


# ===================================================================
# Model & RAG initialisation (lazy, runs once)
# ===================================================================

_initialised = False
_llm = None
_llm_with_tools = None
_query_engine_gold = None
_query_engine_other = None
_query_engine_info = None
_rag_backend = "uninitialised"
_fallback_corpora = {}
_tools = []
_tool_map = {}

# Model configuration — matches agent.ipynb
OPENAI_MODEL_ID = "gpt-4o-mini"


def _chroma_source_fingerprint() -> str:
    if not _DIR_CHROMADB.exists() or not any(_DIR_CHROMADB.iterdir()):
        raise RuntimeError("chroma_index_missing")

    digest = hashlib.sha256()
    for entry in sorted(_DIR_CHROMADB.rglob("*")):
        relative = str(entry.relative_to(_DIR_CHROMADB)).replace("\\", "/")
        digest.update(relative.encode("utf-8"))
        if entry.is_file():
            stats = entry.stat()
            digest.update(str(stats.st_size).encode("utf-8"))
            digest.update(str(int(stats.st_mtime)).encode("utf-8"))
    return digest.hexdigest()[:12]


@lru_cache(maxsize=1)
def _runtime_chromadb_path() -> Path:
    runtime_dir = _RUNTIME_ROOT / f"chromadb_{_chroma_source_fingerprint()}"
    if not runtime_dir.exists() or not any(runtime_dir.iterdir()):
        runtime_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(_DIR_CHROMADB, runtime_dir, dirs_exist_ok=True)

    probe_path = runtime_dir / ".leaseguard_write_test"
    probe_path.write_text("ok", encoding="utf-8")
    probe_path.unlink(missing_ok=True)
    return runtime_dir


_RAG_DATA_DIRECTORIES = {
    "gold_standard_leases": _DIR_RAG_DATA / "gold_standard_leases",
    "other_leases": _DIR_RAG_DATA / "other_leases",
    "lease_info": _DIR_RAG_DATA / "info",
}


def _reference_text_from_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)

    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="ignore")


def _retrieval_tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z]{4,}", text.lower())}


@lru_cache(maxsize=1)
def _load_fallback_corpora() -> dict[str, list[dict[str, Any]]]:
    splitter = SentenceSplitter(chunk_size=700, chunk_overlap=80)
    corpora: dict[str, list[dict[str, Any]]] = {}

    for collection_name, directory in _RAG_DATA_DIRECTORIES.items():
        if not directory.exists():
            raise RuntimeError(f"missing_rag_data_{collection_name}")

        chunks: list[dict[str, Any]] = []
        for file_path in sorted(directory.rglob("*")):
            if not file_path.is_file():
                continue
            text = _normalize_prompt_text(_reference_text_from_path(file_path))
            if not text:
                continue
            for chunk in splitter.split_text(text):
                tokens = _retrieval_tokens(chunk)
                if not tokens:
                    continue
                chunks.append(
                    {
                        "source": file_path.name,
                        "text": chunk[:1400],
                        "tokens": tokens,
                    }
                )
        corpora[collection_name] = chunks

    return corpora


def _lexical_rag_search(chunks: list[dict[str, Any]], query: str, top_k: int = 4, max_chars: int = 3200) -> str:
    query_text = _normalize_prompt_text(query)
    query_tokens = _retrieval_tokens(query_text)
    if not chunks or not query_tokens:
        return "No relevant reference material found."

    scored: list[tuple[int, dict[str, Any]]] = []
    lowered_query = query_text.lower()
    for chunk in chunks:
        overlap = len(query_tokens & chunk["tokens"])
        if overlap <= 0:
            continue
        lowered_chunk = chunk["text"].lower()
        score = overlap * 8
        if lowered_query and lowered_query[:120] in lowered_chunk:
            score += 40
        score += sum(2 for token in query_tokens if token in lowered_chunk)
        scored.append((score, chunk))

    if not scored:
        return "No relevant reference material found."

    scored.sort(key=lambda item: item[0], reverse=True)
    lines: list[str] = []
    total_chars = 0
    for _score, chunk in scored[:top_k]:
        snippet = f"[{chunk['source']}]\n{chunk['text']}"
        total_chars += len(snippet)
        if total_chars > max_chars:
            break
        lines.append(snippet)

    return "\n\n---\n\n".join(lines) if lines else "No relevant reference material found."


@lru_cache(maxsize=1)
def _resolve_rag_backend() -> tuple[str, str]:
    _ensure_api_keys()

    try:
        runtime_dir = _runtime_chromadb_path()
        chroma_client = chromadb.PersistentClient(path=str(runtime_dir))
        for collection_name in _RAG_DATA_DIRECTORIES:
            chroma_client.get_collection(collection_name)
        return "chroma", ""
    except RuntimeError as exc:
        chroma_error = str(exc)
    except Exception as exc:
        chroma_error = str(exc).strip() or "chroma_runtime_bootstrap_failed"

    try:
        corpora = _load_fallback_corpora()
        if all(corpora.get(name) for name in _RAG_DATA_DIRECTORIES):
            return "local_fallback", ""
    except Exception:
        pass

    return "unavailable", chroma_error or "live_stack_unavailable"


@lru_cache(maxsize=1)
def check_live_audit_ready() -> tuple[bool, str]:
    backend, error_code = _resolve_rag_backend()
    if backend in {"chroma", "local_fallback"}:
        return True, ""
    return False, error_code or "live_stack_unavailable"


def _init_models_and_rag():
    """One-time setup of LLM, embeddings, and RAG query engines."""
    global _initialised, _llm, _llm_with_tools
    global _query_engine_gold, _query_engine_other, _query_engine_info
    global _rag_backend, _fallback_corpora, _tools, _tool_map

    if _initialised:
        return

    _ensure_api_keys()

    _llm = ChatOpenAI(model=OPENAI_MODEL_ID, temperature=0)

    Settings.llm = LlamaIndexOpenAI(model=OPENAI_MODEL_ID)
    Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small")

    backend, error_code = _resolve_rag_backend()
    if backend == "unavailable":
        raise RuntimeError(error_code or "live_stack_unavailable")

    _rag_backend = backend
    if backend == "chroma":
        chroma_client = chromadb.PersistentClient(path=str(_runtime_chromadb_path()))

        def _load_index(collection_name):
            collection = chroma_client.get_collection(collection_name)
            vector_store = ChromaVectorStore(chroma_collection=collection)
            return VectorStoreIndex.from_vector_store(
                vector_store, embed_model=Settings.embed_model
            )

        _query_engine_gold = _load_index("gold_standard_leases").as_query_engine()
        _query_engine_other = _load_index("other_leases").as_query_engine()
        _query_engine_info = _load_index("lease_info").as_query_engine()
    else:
        _fallback_corpora = _load_fallback_corpora()

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

def _query_reference_material(collection_name: str, query: str) -> str:
    if _rag_backend == "chroma":
        if collection_name == "gold_standard_leases":
            return str(_query_engine_gold.query(query))
        if collection_name == "other_leases":
            return str(_query_engine_other.query(query))
        return str(_query_engine_info.query(query))

    return _lexical_rag_search(_fallback_corpora.get(collection_name, []), query)


@tool
def retrieve_gold_standard_clauses(query: str) -> str:
    """Retrieve examples of fair, standard lease language from gold standard
    lease templates for comparison."""
    return _query_reference_material("gold_standard_leases", query)


@tool
def retrieve_other_lease_examples(query: str) -> str:
    """Retrieve examples from real-world lease agreements for comparison."""
    return _query_reference_material("other_leases", query)


@tool
def retrieve_lease_info(query: str) -> str:
    """Retrieve educational information about lease red flags, illegal clauses,
    and best practices for tenants."""
    return _query_reference_material("lease_info", query)


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
    prepare_network_env()
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
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict):
    _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


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


def _call_llm(messages, stage_name=None):
    cache = _load_cache()
    if stage_name:
        content_str = "|".join(m.content for m in messages if hasattr(m, "content"))
        content_hash = hashlib.sha256(content_str.encode()).hexdigest()[:32]
        ck = _cache_key(stage_name, content_hash)
        if ck in cache:
            return cache[ck]
    else:
        ck = None

    result = _llm.invoke(messages).content

    if ck:
        cache[ck] = result
        _save_cache(cache)

    return result


# ===================================================================
# Lease ingestion
# ===================================================================

def _normalize_prompt_text(text: str) -> str:
    cleaned = text or ""
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _truncate_text(text: str, max_chars: int) -> str:
    cleaned = _normalize_prompt_text(text)
    if len(cleaned) <= max_chars:
        return cleaned

    truncated = cleaned[:max_chars].rsplit(" ", 1)[0].strip()
    return f"{truncated}\n\n[Truncated for model input]"


def _split_long_block(block: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", block)
        if sentence.strip()
    ]
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        sentence_len = len(sentence) + (1 if current else 0)
        if current and current_len + sentence_len > max_chars:
            pieces.append(" ".join(current).strip())
            current = [sentence]
            current_len = len(sentence)
            continue

        if not current and len(sentence) > max_chars:
            pieces.append(_truncate_text(sentence, max_chars))
            current_len = 0
            continue

        current.append(sentence)
        current_len += sentence_len

    if current:
        pieces.append(" ".join(current).strip())

    return pieces


def _split_contract_chunks(contract_text: str, max_chars: int = 25000) -> list[str]:
    normalized = _normalize_prompt_text(contract_text)
    if len(normalized) <= max_chars:
        return [normalized]

    numbered_blocks = [
        block.strip()
        for block in re.split(r"(?=\n?\d+[.)]\s+)", normalized)
        if block.strip()
    ]
    blocks = numbered_blocks if len(numbered_blocks) > 1 else [
        block.strip()
        for block in re.split(r"\n\s*\n", normalized)
        if block.strip()
    ]

    if not blocks:
        return [_truncate_text(normalized, max_chars)]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush_current():
        nonlocal current, current_len
        if current:
            chunks.append("\n\n".join(current).strip())
            current = []
            current_len = 0

    for block in blocks:
        if len(block) > max_chars:
            flush_current()
            chunks.extend(_split_long_block(block, max_chars))
            continue

        addition = len(block) + (2 if current else 0)
        if current and current_len + addition > max_chars:
            flush_current()

        current.append(block)
        current_len += addition

    flush_current()
    return chunks or [_truncate_text(normalized, max_chars)]


def ingest_user_lease(file_path: str) -> str:
    """Extract text from a lease PDF/DOCX and normalize it for LLM review."""
    text = ""

    try:
        try:
            from frontend.audit_backend import extract_lease_text
        except ImportError:
            from audit_backend import extract_lease_text
        text = extract_lease_text(file_path)
    except Exception:
        documents = SimpleDirectoryReader(input_files=[file_path]).load_data()
        text = "\n\n".join(doc.text for doc in documents)

    return _normalize_prompt_text(text)


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


def _finding_key(finding: dict[str, Any]) -> str:
    clause_name = str(finding.get("clause_name") or "").lower()
    clause_name = re.sub(r"[^a-z0-9]+", " ", clause_name).strip()
    if clause_name:
        return clause_name

    raw_output = _normalize_prompt_text(str(finding.get("raw_output") or ""))
    if raw_output:
        return hashlib.sha256(raw_output.encode()).hexdigest()[:16]

    return hashlib.sha256(repr(sorted(finding.items())).encode()).hexdigest()[:16]


def _dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    ordered_keys: list[str] = []

    for finding in findings:
        key = _finding_key(finding)
        if key not in deduped:
            deduped[key] = finding
            ordered_keys.append(key)
            continue

        existing = deduped[key]
        existing_severity = existing.get("severity") or -1
        new_severity = finding.get("severity") or -1
        if new_severity > existing_severity:
            deduped[key] = finding

    return [deduped[key] for key in ordered_keys]


def _format_findings_for_prompt(
    findings: list[dict[str, Any]],
    *,
    include_fair: bool,
    max_items: int = 40,
) -> str:
    lines: list[str] = []

    for finding in findings:
        label = str(finding.get("label") or "unknown").strip().lower()
        if not include_fair and label == "fair":
            continue

        clause_name = str(finding.get("clause_name") or "Unnamed clause").strip()
        severity = finding.get("severity")
        severity_text = str(int(severity)) if isinstance(severity, (int, float)) else "n/a"
        explanation_source = (
            str(finding.get("explanation") or "").strip()
            or str(finding.get("raw_output") or "").strip()
        )
        explanation = _truncate_text(explanation_source, 260).replace("\n", " ")
        lines.append(
            "\n".join(
                [
                    f"- Clause: {clause_name}",
                    f"  Label: {label}",
                    f"  Severity: {severity_text}",
                    f"  Explanation: {explanation}",
                ]
            )
        )
        if len(lines) >= max_items:
            break

    return "\n\n".join(lines) if lines else "- No non-fair findings were identified."


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
            f"Lease Contract to Review:\n{_truncate_text(contract_text, 12000)}\n\n"
            "Using the tools available to you, retrieve relevant legal standards "
            "and gold standard lease language for this jurisdiction. Then establish "
            "the baseline standards against which this lease will be evaluated."
        )),
    ]
    return _call_llm_with_tools(messages, stage_name="define_standards_v2")


def _stage_analyze_clauses(
    contract_text: str,
    standards: str,
    city: str,
    state: str,
    on_chunk: Optional[Callable[[int, int], None]] = None,
):
    standards_excerpt = _truncate_text(standards, 6000)
    chunks = _split_contract_chunks(contract_text)
    raw_parts: list[str] = []

    for index, chunk in enumerate(chunks, start=1):
        if on_chunk:
            on_chunk(index, len(chunks))

        messages = [
            SystemMessage(content=PROMPT_ANALYZE_CLAUSES),
            HumanMessage(content=(
                f"Renter's Location: {city}, {state}\n\n"
                f"Standards Framework:\n{standards_excerpt}\n\n"
                f"Lease Contract Excerpt ({index}/{len(chunks)}):\n{chunk}\n\n"
                "Analyze each clause in this lease excerpt. Only analyze clauses that "
                "appear in this excerpt. If a clause is clearly duplicated boilerplate, "
                "do not repeat it unnecessarily.\n\n"
                "For each clause, provide:\n"
                "- **Clause**: name/description\n"
                "- **Label**: illegal / unfair but legal / unclear or ambiguous / outdated / fair\n"
                "- **Severity**: score from 1 (minor) to 10 (critical)\n"
                "- **Explanation**: why this clause received this label\n\n"
                "Use the tools to verify against legal standards and gold standard language."
            )),
        ]
        raw_parts.append(
            _call_llm(
                messages,
                stage_name=f"analyze_clauses_v3_chunk_{index}_of_{len(chunks)}",
            )
        )

    raw_analysis = "\n\n".join(part for part in raw_parts if part)
    findings = _dedupe_findings(parse_clause_analysis(raw_analysis))
    return findings, raw_analysis


def _stage_prioritize(findings, standards: str, city: str, state: str) -> str:
    standards_excerpt = _truncate_text(standards, 5000)
    findings_summary = _format_findings_for_prompt(findings, include_fair=False)
    messages = [
        SystemMessage(content=PROMPT_PRIORITIZE_FINDINGS),
        HumanMessage(content=(
            f"Renter's Location: {city}, {state}\n\n"
            f"Standards Framework:\n{standards_excerpt}\n\n"
            f"Clause Analysis Results:\n{findings_summary}\n\n"
            "Rank the findings above by priority. Consider:\n"
            "- Legal risk (illegal clauses first)\n"
            "- Financial impact on the renter\n"
            "- Likelihood of successful negotiation\n"
            "- Long-term consequences\n"
            "Provide a numbered priority list with justification for the ranking."
        )),
    ]
    return _call_llm(messages, stage_name="prioritize_findings_v3")


def _stage_generate_report(
    contract_text: str, findings, standards: str,
    prioritized: str, city: str, state: str,
) -> str:
    standards_excerpt = _truncate_text(standards, 4000)
    contract_excerpt = _truncate_text(contract_text, 4000)
    findings_summary = _format_findings_for_prompt(findings, include_fair=True)
    prioritized_excerpt = _truncate_text(prioritized, 5000)
    messages = [
        SystemMessage(content=PROMPT_GENERATE_REPORT),
        HumanMessage(content=(
            f"Renter's Location: {city}, {state}\n\n"
            f"Lease Overview:\n{contract_excerpt}\n\n"
            f"Standards Framework:\n{standards_excerpt}\n\n"
            f"Clause Analysis:\n{findings_summary}\n\n"
            f"Prioritized Findings:\n{prioritized_excerpt}\n\n"
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
    return _call_llm(messages, stage_name="generate_report_v3")


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

    def _chunk_status(index: int, total: int):
        _status(f"Stage 2/4: Analyzing lease clauses ({index}/{total})...")

    findings, raw_analysis = _stage_analyze_clauses(
        contract_text,
        standards,
        city,
        state,
        on_chunk=_chunk_status,
    )

    _status("Stage 3/4: Prioritizing findings...")
    prioritized = _stage_prioritize(findings, standards, city, state)

    _status("Stage 4/4: Generating improvement report...")
    report = _stage_generate_report(contract_text, findings, standards, prioritized, city, state)

    return {
        "report": report,
        "findings": findings,
        "standards": standards,
        "prioritized": prioritized,
        "raw_analysis": raw_analysis,
    }
