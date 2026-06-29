"""Backend helpers for LeaseGuard audit flows."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import re
import zipfile
from functools import lru_cache
from mimetypes import guess_type
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET

try:
    from frontend.secrets_utils import get_secret, prepare_network_env
except ImportError:
    from secrets_utils import get_secret, prepare_network_env


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FINDINGS_PATH = PROJECT_ROOT / "agent" / "clause_findings.csv"
SAMPLE_REPORT_PATH = PROJECT_ROOT / "agent" / "improvement_decision_report.md"
SAMPLE_READER_SECTIONS_PATH = PROJECT_ROOT / "agent" / "sample_reader_sections.json"
SAMPLE_LEASE_PATH = PROJECT_ROOT / "rag_data" / "other_leases" / "Chicago Sublease.pdf"


class LeaseTextExtractionError(RuntimeError):
    """Raised when a lease file cannot be parsed into reliable readable text."""

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
    "�": '"',
}


def _clean_text(value: str | None) -> str:
    text = value or ""
    for old, new in MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(old, new)
    return text.strip()


def _read_text_best_effort(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _normalize_spacing(text: str) -> str:
    cleaned = _clean_text(text)
    cleaned = re.sub(r"\r\n?", "\n", cleaned)
    cleaned = cleaned.replace("\u2003", " ")
    cleaned = re.sub(r"[ \t\u00A0\u2000-\u200B]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _clean_report_text(text: str) -> str:
    cleaned = _normalize_spacing(text)
    cutoff_markers = [
        "What you can do next (practical steps)",
        "If you'd like, I can draft",
        "If you'd like, I can produce",
        "Would you like me to proceed",
        "Notes on the tools and sources used",
    ]
    cut_positions = [cleaned.find(marker) for marker in cutoff_markers if marker in cleaned]
    if cut_positions:
        cleaned = cleaned[: min(cut_positions)].rstrip()
    return cleaned


def _mime_type_for_path(path: Path) -> str | None:
    mime_type, _ = guess_type(str(path))
    return mime_type


def _build_source_metadata(path: Path | None) -> dict[str, Any]:
    if not path:
        return {
            "source_name": "",
            "source_mime_type": None,
            "source_bytes": None,
        }

    source_bytes = path.read_bytes() if path.exists() else None
    return {
        "source_name": path.name,
        "source_mime_type": _mime_type_for_path(path),
        "source_bytes": source_bytes,
    }


def _translation_unavailable_text() -> str:
    return "Plain-English translation unavailable for this section right now."


def _validate_reader_sections_payload(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("Sample translation artifact is missing or malformed.")

    sections: list[dict[str, str]] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise RuntimeError("Sample translation artifact is missing or malformed.")

        title = _normalize_reader_title(str(item.get("title") or ""), index)
        original_text = _clean_original_text(str(item.get("original_text") or ""))
        plain_english = _normalize_spacing(str(item.get("plain_english") or "")).strip("\"' ")

        if not title or not original_text or not plain_english:
            raise RuntimeError("Sample translation artifact is missing or malformed.")

        sections.append(
            {
                "title": title,
                "original_text": original_text,
                "plain_english": plain_english,
            }
        )

    return sections


def _load_sample_reader_sections() -> list[dict[str, str]]:
    payload = json.loads(_read_text_best_effort(SAMPLE_READER_SECTIONS_PATH))
    return _validate_reader_sections_payload(payload)


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _load_api_key() -> str | None:
    return get_secret("OPENAI_API_KEY", project_root=PROJECT_ROOT)


def _can_use_translation_llm() -> bool:
    return bool(_load_api_key()) and _module_available("openai")


def _normalize_reader_title(title: str, index: int | None = None) -> str:
    cleaned = _normalize_clause_name(title)
    cleaned = re.sub(r"^\d+[.)]\s*", "", cleaned)
    cleaned = cleaned.strip(" .:-\u2014\u2013\"'")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if cleaned.isupper() and len(cleaned) <= 80:
        cleaned = cleaned.title()
    if len(cleaned) > 80 and "(" in cleaned:
        cleaned = re.sub(r"\s*\([^)]*\)", "", cleaned).strip()
    if not cleaned:
        return f"Section {index}" if index is not None else "Lease section"
    return cleaned


def _clean_original_text(text: str) -> str:
    cleaned = _normalize_spacing(text)
    cleaned = cleaned.strip("\"' ")
    cleaned = re.sub(r"^[\u201c\u201d\u2018\u2019]+", "", cleaned)
    cleaned = re.sub(r"[\u201c\u201d\u2018\u2019]+$", "", cleaned)
    cleaned = cleaned.replace(" ;", ";")
    return cleaned.strip()


def _strip_extracted_noise(text: str) -> str:
    cleaned = _normalize_spacing(text)
    cleaned = cleaned.replace("-\n", "")
    lines: list[str] = []
    cutoff_markers = (
        "why is this brochure being provided to me?",
        "chicago department of public health",
        "preventing infestations in apartments",
        "this brochure provides information on bed bugs",
    )
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if not line:
            lines.append("")
            continue
        if any(marker in lowered for marker in cutoff_markers):
            break
        if re.fullmatch(r"page\s+\d+\s+of\s+\d+", lowered):
            continue
        if re.fullmatch(r"\d+\s*/\s*\d+", line):
            continue
        if line.startswith("?"):
            continue
        if "national apartment association" in lowered and len(line) <= 120:
            continue
        if re.fullmatch(r"[_|\- ]{4,}", line):
            continue
        lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def _extract_clause_text(raw_output: str) -> str | None:
    match = re.search(
        r"- Clause:\s*(.+?)(?=\n-\s*(?:Label|Severity|Explanation|Legal)|$)",
        raw_output,
        re.DOTALL,
    )
    if not match:
        return None

    clause_text = _clean_original_text(match.group(1))
    return clause_text or None


def _replace_party_terms(text: str) -> str:
    updated = text
    updated = re.sub(r"\bwe\b", "the landlord", updated, flags=re.IGNORECASE)
    updated = re.sub(r"\bus\b", "the landlord", updated, flags=re.IGNORECASE)
    updated = re.sub(r"\bour\b", "the landlord's", updated, flags=re.IGNORECASE)
    return updated

def _normalize_clause_name(title: str) -> str:
    cleaned = _clean_text(title)
    replacements = {
        "Movein": "Move-in",
        "Moveout": "Move-out",
        "Posttreatment": "Post-treatment",
        "Pretreatment": "Pre-treatment",
    }
    for old, new in replacements.items():
        cleaned = re.sub(rf"\b{old}\b", new, cleaned, flags=re.IGNORECASE)
    return cleaned


def _heading_title_from_line(line: str, index: int) -> tuple[str, str]:
    cleaned = _normalize_spacing(line)
    patterns = [
        r"^(?:[A-Z]|\d{1,2})\.\s+(?P<title>[A-Z][A-Za-z0-9/&(),'\- ]{2,100}?)\.\s+(?P<body>.+)$",
        r"^(?:[A-Z]|\d{1,2})\.\s+(?P<title>[A-Z][A-Za-z0-9/&(),'\- ]{2,100})(?:\.\s*)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, cleaned)
        if not match:
            continue
        title = _normalize_reader_title(match.group("title"), index)
        body = _clean_original_text(match.groupdict().get("body") or "")
        return title, body
    return _normalize_reader_title(cleaned, index), ""


def _expand_inline_headings(text: str) -> str:
    expanded = text
    expanded = re.sub(
        r"(?<![A-Za-z0-9])(?=(?:[A-Z]\.\s+[A-Z][A-Za-z][A-Za-z0-9/&(),'\- ]{2,90}\.))",
        "\n",
        expanded,
    )
    expanded = re.sub(
        r"(?<![A-Za-z0-9])(?=(?:\d{1,2}\.\s+[A-Z][A-Z0-9/&(),'\- ]{3,120}\.))",
        "\n",
        expanded,
    )
    return re.sub(r"\n{3,}", "\n\n", expanded)

def _refine_reader_title(title: str, original_text: str, index: int) -> str:
    normalized = _normalize_reader_title(title, index)
    lowered_title = normalized.lower()
    lowered_text = original_text.lower()

    inferred_titles = [
        ("security deposit", "Security Deposit"),
        ("late fee", "Late Fee"),
        ("rent", "Rent"),
        ("term of this agreement", "Lease Term"),
        ("utilities", "Utilities"),
        ("pets", "Pets"),
        ("repair", "Repairs and Maintenance"),
        ("maintenance", "Repairs and Maintenance"),
        ("sublet", "Subletting"),
        ("default", "Default"),
        ("insurance", "Insurance"),
        ("parking", "Parking"),
        ("appliance", "Appliances"),
        ("entry", "Entry and Access"),
        ("access", "Entry and Access"),
        ("renewal", "Renewal"),
        ("termination", "Termination"),
    ]

    if normalized.startswith("$") or "_" in normalized or len(re.sub(r"[^A-Za-z]", "", normalized)) <= 3:
        inferred_title, _ = _heading_title_from_line(original_text, index)
        if inferred_title and inferred_title != f"Section {index}":
            return inferred_title
        for needle, label in inferred_titles:
            if needle in lowered_text:
                return label

    if lowered_title in {"state of minnesota )", "county of )"}:
        return normalized.split(")", 1)[0].replace("(", "").strip()

    return normalized

def _should_skip_reader_section(title: str, original_text: str) -> bool:
    lowered_title = title.lower().strip()
    lowered_text = original_text.lower()

    if lowered_title.isdigit():
        return True
    if lowered_text in {"or", "ss."}:
        return True
    if lowered_title in {"by", "by:", "date", "witness", "witnesseth"}:
        return True
    if lowered_title.startswith("state of") and "acknowledg" in lowered_text:
        return True
    if lowered_title.startswith("county of") and "acknowledg" in lowered_text:
        return True
    if len(re.findall(r"[A-Za-z]", original_text)) < 25:
        return True

    signature_terms = [
        "acknowledged before me",
        "signature of person taking acknowledgement",
        "notary public",
        "its president",
        "personally appeared",
        "how can bed bugs get into an apartment",
        "what should i do if i suspect there are bed bugs",
    ]
    if any(term in lowered_text for term in signature_terms):
        return True

    return False

def _plain_english_points(text: str, max_points: int = 3) -> list[str]:
    cleaned = _clean_original_text(text)
    if not cleaned:
        return []

    prelude, sep, remainder = cleaned.partition(":")
    if sep and ";" in remainder and len(prelude.split()) <= 8:
        cleaned = remainder

    parts = [
        part.strip(" \"'")
        for part in re.split(r";\s+|(?<=[.!?])\s+", cleaned)
        if part.strip(" \"'")
    ]

    points: list[str] = []
    for part in parts:
        updated = _replace_party_terms(part)
        updated = re.sub(r"^(and|or)\s+", "", updated, flags=re.IGNORECASE)
        updated = re.sub(r"\byou represent that\b", "you confirm that", updated, flags=re.IGNORECASE)
        updated = re.sub(r"\byou shall\b", "you must", updated, flags=re.IGNORECASE)
        updated = re.sub(r"\bshall\b", "must", updated, flags=re.IGNORECASE)
        updated = re.sub(r"\bhereby\b", "", updated, flags=re.IGNORECASE)
        updated = re.sub(r"\s+", " ", updated).strip(" ,")

        if not updated:
            continue
        if len(updated.split()) <= 2:
            continue
        updated = updated[0].upper() + updated[1:]
        if updated[-1] not in ".!?":
            updated += "."
        points.append(updated)
        if len(points) >= max_points:
            break

    if len(points) >= 2 and len(points[-1].split()) <= 3:
        points = points[:-1]
    return points


def _first_sentences(text: str, limit: int = 2) -> str:
    pieces = re.split(r"(?<=[.!?])\s+", _clean_original_text(text))
    return " ".join(piece for piece in pieces[:limit] if piece)


def _title_guided_summary(title: str, text: str) -> str | None:
    lowered_title = title.lower()
    lowered_text = text.lower()

    if "purpose" in lowered_title or "intro" in lowered_title:
        return "The addendum explains that this document is about bed bug prevention and relies on what the tenant discloses at move-in."
    if "move-in" in lowered_title or "initial representations" in lowered_title:
        return "You are confirming what you know about bed bugs before move-in and agreeing to report any signs quickly after you move in."
    if "access" in lowered_title or "inspection" in lowered_title:
        return "The landlord and pest-control workers can enter the unit at reasonable times to inspect or treat for bed bugs, and you have to cooperate."
    if "notice" in lowered_title or "report" in lowered_title:
        return "You have to tell the landlord in writing if you suspect or discover bed bugs in the unit or your belongings."
    if "cooperation" in lowered_title or "treatment" in lowered_title:
        return "You have to follow treatment instructions and prepare the apartment the way the landlord or pest-control team requires."
    if "transfer" in lowered_title or "relocation" in lowered_title:
        return "This clause explains when the landlord can move you to another unit and what conditions apply if bed bugs affect the apartment."
    if "apartment description" in lowered_title or "premises" in lowered_title:
        return "This clause identifies the unit and address covered by the lease."
    if "lease contract description" in lowered_title:
        return "The addendum becomes part of the lease and controls if it conflicts with the main lease."
    if "rent" in lowered_title and "payment" in lowered_text:
        return "This clause explains how much rent you owe and when payment is due."
    return None


def _plain_english_summary(text: str, title: str = "", sentence_limit: int = 2) -> str:
    cleaned = _clean_original_text(text)
    guided = _title_guided_summary(title, cleaned)
    if guided:
        return guided

    points = _plain_english_points(cleaned, max_points=sentence_limit)
    if points:
        return " ".join(points[:sentence_limit]).strip()

    fallback = _first_sentences(cleaned, limit=sentence_limit).strip()
    if fallback:
        return fallback

    return "This clause explains a lease rule that affects your responsibilities or the landlord's rights."


def _sections_to_markdown(sections: list[dict[str, str]]) -> str:
    if not sections:
        return ""

    lines: list[str] = []
    for section in sections:
        lines.append(f"**{section['title']}**")
        lines.append(section["plain_english"])
    return "\n\n".join(lines)


def _extract_json_payload(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None


@lru_cache(maxsize=24)
def _rewrite_sections_with_llm_cached(payload_json: str) -> str | None:
    if not _can_use_translation_llm():
        return None

    from openai import OpenAI

    api_key = _load_api_key()
    if not api_key:
        return None

    prepare_network_env()
    client = OpenAI(api_key=api_key, timeout=10.0, max_retries=1)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0.2,
        max_tokens=2200,
        messages=[
            {
                "role": "system",
                "content": (
                    "You rewrite lease sections into plain English. Return valid JSON only with the shape "
                    "{\"sections\":[{\"index\":1,\"title\":\"...\",\"plain_english\":\"...\"}]}. "
                    "Keep the same order and count. Clean each title so it is short and readable. "
                    "Write each plain_english value as one short paragraph of 1-3 sentences. "
                    "Start with the real effect of the clause, not phrases like 'This section says'. "
                    "Explain the clause directly, clearly, and neutrally. Do not use bullet points. "
                    "Do not mention AI, prompts, or legal advice. Do not copy long legal fragments verbatim."
                ),
            },
            {"role": "user", "content": payload_json},
        ],
    )
    return response.choices[0].message.content or None


def _rewrite_sections_with_llm(sections: list[dict[str, str]]) -> list[dict[str, str]] | None:
    if not sections:
        return []
    if not _can_use_translation_llm():
        return None

    rewritten = [dict(section) for section in sections]
    translated_any = False
    batch_size = 6

    for batch_start in range(0, len(sections), batch_size):
        batch = sections[batch_start: batch_start + batch_size]
        payload_sections = [
            {
                "index": offset + 1,
                "title": section["title"],
                "original_text": section["original_text"][:900],
            }
            for offset, section in enumerate(batch)
        ]

        try:
            raw = _rewrite_sections_with_llm_cached(json.dumps({"sections": payload_sections}, ensure_ascii=False))
        except Exception:
            raw = None
        if not raw:
            if not translated_any:
                break
            continue

        parsed = _extract_json_payload(raw)
        if not parsed or not isinstance(parsed.get("sections"), list):
            if not translated_any:
                break
            continue

        batch_translated = False
        for item in parsed["sections"]:
            if not isinstance(item, dict):
                continue
            try:
                translated_index = int(item.get("index")) - 1
            except (TypeError, ValueError):
                continue
            if translated_index < 0 or translated_index >= len(batch):
                continue

            global_index = batch_start + translated_index
            title = _normalize_reader_title(str(item.get("title") or ""), global_index + 1)
            plain = _normalize_spacing(str(item.get("plain_english") or "")).strip("\"' ")
            if not plain:
                continue

            rewritten[global_index] = {
                "title": title or rewritten[global_index]["title"],
                "original_text": rewritten[global_index]["original_text"],
                "plain_english": plain,
            }
            translated_any = True
            batch_translated = True

        if not batch_translated and not translated_any:
            break

    if not translated_any:
        return None

    for section in rewritten:
        plain = str(section.get("plain_english") or "").strip()
        if plain:
            continue
        section["plain_english"] = _translation_unavailable_text()

    return rewritten


def _build_reader_sections(raw_sections: list[dict[str, str]]) -> tuple[list[dict[str, str]], str]:
    sections: list[dict[str, str]] = []
    for index, raw_section in enumerate(raw_sections, start=1):
        original_text = _clean_original_text(raw_section.get("original_text") or raw_section.get("body") or "")
        if not original_text:
            continue
        title = _refine_reader_title(str(raw_section.get("title") or ""), original_text, index)
        if _should_skip_reader_section(title, original_text):
            continue
        sections.append({"title": title, "original_text": original_text, "plain_english": ""})

    rewritten_sections = _rewrite_sections_with_llm(sections)
    if rewritten_sections is not None:
        return rewritten_sections, "live_llm"

    if _can_use_translation_llm():
        for section in sections:
            section["plain_english"] = _translation_unavailable_text()
        return sections, "fallback_unavailable"

    for section in sections:
        section["plain_english"] = _plain_english_summary(section["original_text"], title=section["title"])
    return sections, "fallback_unavailable"


def _reader_sections_from_findings(findings: list[dict[str, Any]]) -> tuple[list[dict[str, str]], str]:
    raw_sections: list[dict[str, str]] = []
    for index, finding in enumerate(findings, start=1):
        clause_text = _extract_clause_text(str(finding.get("raw_output", "")))
        if not clause_text:
            continue
        clause_name = _normalize_clause_name(str(finding.get("clause_name") or f"Clause {index}"))
        raw_sections.append({"title": clause_name, "original_text": clause_text})
    return _build_reader_sections(raw_sections)


def _reader_sections_from_text(text: str) -> tuple[list[dict[str, str]], str]:
    raw_sections: list[dict[str, str]] = []
    for section in _split_lease_sections(text):
        raw_sections.append({"title": section["title"], "original_text": section["body"]})
    return _build_reader_sections(raw_sections)

def build_cleaned_lease_text(findings: list[dict[str, Any]]) -> str:
    sections: list[str] = []
    for index, finding in enumerate(findings, start=1):
        clause_text = _extract_clause_text(str(finding.get("raw_output", "")))
        if not clause_text:
            continue
        clause_name = _normalize_clause_name(str(finding.get("clause_name") or f"Clause {index}"))
        sections.append(f"{index}. {clause_name}\n{clause_text}")

    if not sections:
        return "A cleaned text version of the lease will appear here after analysis."
    return "\n\n".join(sections)


def build_plain_language_translation(findings: list[dict[str, Any]]) -> str:
    sections, _ = _reader_sections_from_findings(findings)
    return _sections_to_markdown(sections)


def _extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages)




def _pdf_text_is_supported(text: str) -> bool:
    cleaned = _strip_extracted_noise(text)
    words = re.findall(r"[A-Za-z]{2,}", cleaned)
    letters = len(re.findall(r"[A-Za-z]", cleaned))
    long_words = [word for word in words if len(word) >= 4]
    return letters >= 180 and len(words) >= 35 and len(long_words) >= 20


def _validate_pdf_text(text: str) -> str:
    if _pdf_text_is_supported(text):
        return text
    raise LeaseTextExtractionError(
        "LeaseGuard works best with text-based PDFs. This PDF looks like a scan or its text could not be extracted cleanly."
    )
def _extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        parts = [node.text or "" for node in paragraph.findall(".//w:t", namespace)]
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def extract_lease_text(file_path: str) -> str:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _validate_pdf_text(_extract_pdf_text(path))
    if suffix == ".docx":
        return _extract_docx_text(path)
    return _read_text_best_effort(path)


def _section_title(text: str, index: int) -> str:
    first_line = text.splitlines()[0].strip()
    return _normalize_reader_title(first_line, index)


def _is_section_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if re.match(r"^(?:[A-Z]|\d{1,2})\.\s+[A-Z]", stripped):
        return True
    if len(stripped) > 100:
        return False
    if stripped.endswith(":"):
        return False
    if re.match(r"^\d+[.)]\s+", stripped):
        return True

    alpha_only = re.sub(r"[^A-Za-z]", "", stripped)
    if alpha_only and alpha_only.isupper() and len(alpha_only) >= 4:
        return True

    words = stripped.split()
    if 1 <= len(words) <= 6 and stripped == stripped.title() and len(stripped) <= 50:
        if re.fullmatch(r"[A-Za-z][A-Za-z '&/\-]{1,50}", stripped):
            return True
    return False


def _section_from_lines(lines: list[str], index: int) -> dict[str, str] | None:
    cleaned_lines = [line.strip() for line in lines if line.strip()]
    if not cleaned_lines:
        return None

    title, inline_body = _heading_title_from_line(cleaned_lines[0], index)
    body_lines = cleaned_lines[1:] if len(cleaned_lines) > 1 else []
    if inline_body:
        body_lines.insert(0, inline_body)
    while body_lines and _normalize_reader_title(body_lines[0], index).lower() == title.lower():
        body_lines = body_lines[1:]

    body = "\n".join(body_lines).strip()
    if not body:
        return None
    return {"title": title, "body": body}

def _split_lease_sections(text: str) -> list[dict[str, str]]:
    normalized = _expand_inline_headings(_strip_extracted_noise(text))
    if not normalized:
        return []

    lines = normalized.splitlines()
    sections: list[dict[str, str]] = []
    current: list[str] = []
    saw_heading = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if current and current[-1] != "":
                current.append("")
            continue

        if _is_section_heading(line):
            if current:
                current_text = " ".join(part for part in current if part).strip()
                if saw_heading or len(current_text) > 220:
                    section = _section_from_lines(current, len(sections) + 1)
                    if section:
                        sections.append(section)
            current = [line]
            saw_heading = True
        else:
            current.append(line)

    if current:
        section = _section_from_lines(current, len(sections) + 1)
        if section:
            sections.append(section)

    if len(sections) <= 1:
        paragraph_blocks = [
            block.strip()
            for block in re.split(r"\n\s*\n", normalized)
            if block.strip()
        ]
        sections = []
        for index, block in enumerate(paragraph_blocks[:36], start=1):
            title = _section_title(block, index)
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            body = "\n".join(lines[1:]).strip() if len(lines) > 1 else lines[0]
            sections.append({"title": title, "body": body or lines[0]})

    return sections[:36]


def build_plain_language_translation_from_text(text: str) -> str:
    sections, _ = _reader_sections_from_text(text)
    if not sections:
        return "Your plain-English translation will appear here after a lease is loaded."
    return _sections_to_markdown(sections)


def run_sample_pipeline(on_stage: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Return a structured audit result from the checked-in sample outputs."""
    if on_stage:
        on_stage("Loading sample lease analysis...")

    findings: list[dict[str, Any]] = []
    findings_text = _read_text_best_effort(SAMPLE_FINDINGS_PATH)
    reader = csv.DictReader(io.StringIO(findings_text))
    for row in reader:
        severity_text = (row.get("severity") or "").strip()
        try:
            severity: float | None = float(severity_text) if severity_text else None
        except ValueError:
            severity = None

        findings.append(
            {
                "clause_name": _clean_text(row.get("clause_name")) or "Unnamed clause",
                "label": _clean_text(row.get("label")).lower() or "unknown",
                "severity": severity,
                "explanation": _clean_text(row.get("explanation")),
                "raw_output": _clean_text(row.get("raw_output")),
            }
        )

    report = _clean_report_text(_read_text_best_effort(SAMPLE_REPORT_PATH))
    reader_sections = _load_sample_reader_sections()
    cleaned_lease_text = build_cleaned_lease_text(findings)
    plain_language_translation = _sections_to_markdown(reader_sections)
    source_metadata = _build_source_metadata(SAMPLE_LEASE_PATH)
    if on_stage:
        on_stage("Sample analysis ready.")

    return {
        "report": report,
        "findings": findings,
        "standards": "Sample backend output",
        "prioritized": "Sample backend output",
        "raw_analysis": "Sample backend output",
        "cleaned_lease_text": cleaned_lease_text,
        "plain_language_translation": plain_language_translation,
        "reader_sections": reader_sections,
        "review_mode": "sample_cached",
        "review_error": "",
        "translation_mode": "sample_cached",
        **source_metadata,
    }


def run_reader_pipeline(file_path: str, on_stage: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Load an uploaded lease into the reader flow without the full audit stack."""
    if on_stage:
        on_stage("Loading lease text...")

    raw_text = extract_lease_text(file_path)
    cleaned_text = _strip_extracted_noise(raw_text)
    reader_sections, translation_mode = _reader_sections_from_text(cleaned_text)
    translation = _sections_to_markdown(reader_sections)
    source_metadata = _build_source_metadata(Path(file_path))

    if on_stage:
        on_stage("Lease text ready.")

    return {
        "report": "",
        "findings": [],
        "standards": "",
        "prioritized": "",
        "raw_analysis": "",
        "cleaned_lease_text": cleaned_text,
        "plain_language_translation": translation,
        "reader_sections": reader_sections,
        "review_mode": "reader_only",
        "review_error": "",
        "translation_mode": translation_mode,
        **source_metadata,
    }


def run_live_pipeline(
    file_path: str,
    city: str,
    state: str,
    on_stage: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the live audit pipeline lazily so sample mode stays lightweight."""
    try:
        from frontend.lease_agent import ingest_user_lease, run_pipeline
    except ImportError:
        from lease_agent import ingest_user_lease, run_pipeline

    contract_text = ingest_user_lease(file_path)
    results = run_pipeline(
        file_path=file_path,
        city=city,
        state=state,
        on_stage=on_stage,
    )
    findings = results.get("findings", [])
    cleaned_contract_text = _strip_extracted_noise(contract_text)
    reader_sections, translation_mode = _reader_sections_from_text(cleaned_contract_text)
    if not reader_sections:
        reader_sections, translation_mode = _reader_sections_from_findings(findings)

    cleaned_lease_text = cleaned_contract_text or build_cleaned_lease_text(findings)
    plain_language_translation = _sections_to_markdown(reader_sections)

    source_metadata = _build_source_metadata(Path(file_path))
    results["report"] = _clean_report_text(str(results.get("report", "")))
    results["cleaned_lease_text"] = cleaned_lease_text
    results["plain_language_translation"] = plain_language_translation
    results["reader_sections"] = reader_sections
    results["review_mode"] = "live_rag"
    results["review_error"] = ""
    results["translation_mode"] = translation_mode
    results.update(source_metadata)
    return results
