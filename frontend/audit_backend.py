"""Backend helpers for LeaseGuard audit flows."""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_FINDINGS_PATH = PROJECT_ROOT / "agent" / "clause_findings.csv"
SAMPLE_REPORT_PATH = PROJECT_ROOT / "agent" / "improvement_decision_report.md"

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
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _extract_clause_text(raw_output: str) -> str | None:
    match = re.search(
        r"- Clause:\s*(.+?)(?=\n-\s*(?:Label|Severity|Explanation|Legal)|$)",
        raw_output,
        re.DOTALL,
    )
    if not match:
        return None

    clause_text = _normalize_spacing(match.group(1))
    return clause_text.strip("\"' ")


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


def _plain_english_points(text: str, max_points: int = 3) -> list[str]:
    cleaned = _normalize_spacing(text).strip("\"' ")
    if not cleaned:
        return []

    prelude, sep, remainder = cleaned.partition(":")
    if sep and ";" in remainder:
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

        updated = updated.strip()
        if not updated:
            continue
        updated = updated[0].upper() + updated[1:]
        if updated[-1] not in ".!?":
            updated += "."
        points.append(updated)
        if len(points) >= max_points:
            break

    return points


def _first_sentences(text: str, limit: int = 2) -> str:
    pieces = re.split(r"(?<=[.!?])\s+", _normalize_spacing(text))
    return " ".join(piece for piece in pieces[:limit] if piece)


def _plain_english_summary(text: str, sentence_limit: int = 2) -> str:
    points = _plain_english_points(text, max_points=sentence_limit)
    if points:
        return " ".join(points[:sentence_limit]).strip()
    return _first_sentences(text, limit=sentence_limit).strip()


def _reader_sections_from_findings(findings: list[dict[str, Any]]) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    for index, finding in enumerate(findings, start=1):
        clause_text = _extract_clause_text(str(finding.get("raw_output", "")))
        if not clause_text:
            continue
        clause_name = _normalize_clause_name(str(finding.get("clause_name") or f"Clause {index}"))
        sections.append(
            {
                "title": clause_name,
                "original_text": clause_text,
                "plain_english": _plain_english_summary(clause_text),
            }
        )
    return sections


def _reader_sections_from_text(text: str) -> list[dict[str, str]]:
    sections: list[dict[str, str]] = []
    for section in _split_lease_sections(text):
        sections.append(
            {
                "title": _normalize_clause_name(section["title"]),
                "original_text": section["body"],
                "plain_english": _plain_english_summary(section["body"]),
            }
        )
    return sections


def _sections_to_markdown(sections: list[dict[str, str]]) -> str:
    if not sections:
        return ""

    lines: list[str] = []
    for section in sections:
        lines.append(f"**{section['title']}**")
        lines.append(section["plain_english"])
    return "\n\n".join(lines)


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
    return _sections_to_markdown(_reader_sections_from_findings(findings))


def _extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[str] = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n\n".join(pages)


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
        return _extract_pdf_text(path)
    if suffix == ".docx":
        return _extract_docx_text(path)
    return _read_text_best_effort(path)


def _section_title(text: str, index: int) -> str:
    first_line = text.splitlines()[0].strip()
    if first_line and len(first_line) <= 100:
        cleaned = re.sub(r"^\d+[.)]\s*", "", first_line).strip()
        return _normalize_clause_name(cleaned)
    return f"Section {index}"


def _split_lease_sections(text: str) -> list[dict[str, str]]:
    normalized = _normalize_spacing(text)
    if not normalized:
        return []

    numbered_blocks = [
        block.strip()
        for block in re.split(r"(?=\n?\d+[.)]\s+)", normalized)
        if block.strip()
    ]
    candidate_blocks = numbered_blocks if len(numbered_blocks) > 1 else [
        block.strip() for block in re.split(r"\n\s*\n", normalized) if block.strip()
    ]

    sections: list[dict[str, str]] = []
    for index, block in enumerate(candidate_blocks[:12], start=1):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        title = _section_title(block, index)
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else lines[0]
        sections.append({"title": title, "body": body or lines[0]})
    return sections


def build_plain_language_translation_from_text(text: str) -> str:
    sections = _reader_sections_from_text(text)
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

    report = _clean_text(_read_text_best_effort(SAMPLE_REPORT_PATH))
    cleaned_lease_text = build_cleaned_lease_text(findings)
    plain_language_translation = build_plain_language_translation(findings)
    reader_sections = _reader_sections_from_findings(findings)
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
    }


def run_reader_pipeline(file_path: str, on_stage: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Load an uploaded lease into the reader flow without the full audit stack."""
    if on_stage:
        on_stage("Loading lease text...")

    raw_text = extract_lease_text(file_path)
    cleaned_text = _normalize_spacing(raw_text)
    translation = build_plain_language_translation_from_text(cleaned_text)
    reader_sections = _reader_sections_from_text(cleaned_text)

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
    cleaned_contract_text = _normalize_spacing(contract_text)
    findings_reader_sections = _reader_sections_from_findings(findings)
    fallback_reader_sections = _reader_sections_from_text(cleaned_contract_text)
    reader_sections = findings_reader_sections or fallback_reader_sections

    cleaned_from_findings = build_cleaned_lease_text(findings)
    if cleaned_from_findings.startswith("A cleaned text version of the lease will appear here"):
        cleaned_lease_text = cleaned_contract_text
    else:
        cleaned_lease_text = cleaned_from_findings

    plain_language_from_findings = build_plain_language_translation(findings)
    if not findings_reader_sections:
        plain_language_translation = build_plain_language_translation_from_text(cleaned_contract_text)
    else:
        plain_language_translation = plain_language_from_findings

    results["cleaned_lease_text"] = cleaned_lease_text
    results["plain_language_translation"] = plain_language_translation
    results["reader_sections"] = reader_sections
    return results
