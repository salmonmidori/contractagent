#!/usr/bin/env python3
"""
rag_data/download_sources.py
============================
Downloads authoritative lease agreements and tenant-rights documents
from public government and legal-aid sources into the appropriate
rag_data/ subfolders.

Three modes of operation per source entry:
  - direct      : fetch a known PDF URL and save it with a fixed filename
  - crawl       : fetch a landing page, extract all relevant PDF links found
                  on that page, and download each one (subject to keyword
                  and domain filtering)
  - turbotenant : for each state slug, fetch the TurboTenant sample page,
                  extract the embedded WordPress PDF attachment ID, and
                  download the attorney-reviewed state lease (all 50 states)

Usage (run from the project root):
    python rag_data/download_sources.py              # download all missing
    python rag_data/download_sources.py --dry-run    # preview, no writes
    python rag_data/download_sources.py --force      # re-download existing
    python rag_data/download_sources.py --verbose    # detailed per-request log

After running this script you must rebuild the ChromaDB index so the
lease agent can see the new documents.  Open agent/agent.ipynb and
re-run the indexing cells.

IMPORTANT: This script only ever writes files inside rag_data/.
           It does not modify any other part of the project.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

# Fix Unicode output on Windows terminals
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from bs4 import BeautifulSoup


# ─────────────────────────────────────────────────────────────────────────────
# Paths  (all writes are confined to RAG_DATA_DIR)
# ─────────────────────────────────────────────────────────────────────────────

RAG_DATA_DIR = Path(__file__).parent          # .../rag_data/
GOLD_DIR     = RAG_DATA_DIR / "gold_standard_leases"
OTHER_DIR    = RAG_DATA_DIR / "other_leases"
INFO_DIR     = RAG_DATA_DIR / "info"

COLLECTION_DIRS: dict[str, Path] = {
    "gold_standard_leases": GOLD_DIR,
    "other_leases":         OTHER_DIR,
    "info":                 INFO_DIR,
}

# Written at the end of every run — records what was downloaded, from where,
# and when.  Useful for auditing the RAG data provenance.
MANIFEST_FILE = RAG_DATA_DIR / "download_manifest.json"


# ─────────────────────────────────────────────────────────────────────────────
# HTTP settings
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    )
}

REQUEST_TIMEOUT = 30           # seconds per request
REQUEST_DELAY   = 1.5          # polite pause between requests
MAX_PDF_BYTES   = 25 * 1024 * 1024   # skip files larger than 25 MB


# ─────────────────────────────────────────────────────────────────────────────
# Relevance filter  (used by crawl mode)
# ─────────────────────────────────────────────────────────────────────────────
# A PDF link found on a crawled page is downloaded only if its href or
# anchor text contains at least one INCLUDE keyword and no EXCLUDE keyword.

INCLUDE_KEYWORDS = [
    "lease", "tenant", "landlord", "rental", "renter", "housing",
    "apartment", "residential", "eviction", "deposit", "rights",
    "ordinance", "habitab", "sublease", "rent", "dwelling",
    "lessee", "lessor", "tenancy",
]

EXCLUDE_KEYWORDS = [
    "permit", "tax-form", "business-license", "zoning", "construction",
    "building-code", "annual-report", "budget", "complaint-form",
    "job-application", "rfp-", "bid-",
]

# Maps TurboTenant URL slug → two-letter state abbreviation.
# Used to produce clean filenames and manifest metadata for the 50-state batch.
STATE_ABBREVIATIONS: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new-hampshire": "NH", "new-jersey": "NJ", "new-mexico": "NM", "new-york": "NY",
    "north-carolina": "NC", "north-dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode-island": "RI", "south-carolina": "SC",
    "south-dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west-virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY",
}


# ─────────────────────────────────────────────────────────────────────────────
# Source manifest
# ─────────────────────────────────────────────────────────────────────────────
# Add new sources here.  To disable a source temporarily, set "enabled": False.
#
# "direct" entry keys:
#   url         – direct URL to a PDF file
#   collection  – "gold_standard_leases" | "other_leases" | "info"
#   filename    – what to save the file as inside the collection folder
#   description – human-readable label
#   state       – two-letter state code or "federal"
#   org         – publishing organization
#   confirmed   – True if the URL was verified live before inclusion;
#                 False = sourced from a web search, treat 404s as expected
#
# "crawl" entry keys (same as above except no "filename"):
#   crawl_domain – if set, only follow PDF links whose host matches this domain
#                  (prevents the crawler from wandering to external sites)

SOURCES: list[dict] = [

    # ── INFO: State AG and government tenant-rights guides ────────────────────
    # These are the most valuable RAG documents: they contain the legal
    # standards the agent uses to evaluate lease clauses.

    {
        "type":        "direct",
        "confirmed":   True,
        "url":         "https://ag.ny.gov/sites/default/files/tenants_rights.pdf",
        "collection":  "info",
        "filename":    "ny_ag_tenants_rights_guide.pdf",
        "description": "New York AG Residential Tenants' Rights Guide",
        "state":       "NY",
        "org":         "New York Attorney General",
    },
    {
        "type":        "direct",
        "confirmed":   True,
        "url":         "https://www.mass.gov/doc/2025-guide-to-landlord-tenant-rights-11182025/download",
        "collection":  "info",
        "filename":    "ma_ag_landlord_tenant_guide_2025.pdf",
        "description": "Massachusetts AG 2025 Guide to Landlord and Tenant Rights",
        "state":       "MA",
        "org":         "Massachusetts Attorney General",
    },
    {
        "type":        "direct",
        "confirmed":   True,
        "url":         "https://dre.ca.gov/files/pdf/2025_Landlord_Tenant_Guide.pdf",
        "collection":  "info",
        "filename":    "ca_dre_landlord_tenant_guide_2025.pdf",
        "description": "California DRE 2025 Guide to Residential Tenants' and Landlords' Rights",
        "state":       "CA",
        "org":         "California Department of Real Estate",
    },
    {
        "type":        "direct",
        "confirmed":   False,
        "url":         "https://oag.ca.gov/system/files/media/Tenant-Protection-Act-Landlords-and-Property-Managers-English.pdf",
        "collection":  "info",
        "filename":    "ca_ag_tenant_protection_act_guide.pdf",
        "description": "California AG Tenant Protection Act Guide",
        "state":       "CA",
        "org":         "California Attorney General",
    },
    {
        "type":        "direct",
        "confirmed":   False,
        "url":         "https://www.ag.idaho.gov/content/uploads/2025/08/LandlordTenant.pdf",
        "collection":  "info",
        "filename":    "idaho_ag_landlord_tenant_manual.pdf",
        "description": "Idaho AG Landlord and Tenant Manual (2025)",
        "state":       "ID",
        "org":         "Idaho Attorney General",
    },
    {
        "type":        "direct",
        "confirmed":   False,
        "url":         "https://dhcd.maryland.gov/Tenant-Landlord-Affairs/Documents/Tenant-Bill-of-Rights-V2.pdf",
        "collection":  "info",
        "filename":    "maryland_tenant_bill_of_rights.pdf",
        "description": "Maryland Tenants' Bill of Rights (effective October 1, 2025)",
        "state":       "MD",
        "org":         "Maryland Department of Housing and Community Development",
    },
    {
        "type":        "direct",
        "confirmed":   False,
        "url":         "https://content.leg.colorado.gov/sites/default/files/renters_rights_-_colorado_law_summary.pdf",
        "collection":  "info",
        "filename":    "colorado_renters_rights_law_summary.pdf",
        "description": "Colorado General Assembly: Renters' Rights Law Summary",
        "state":       "CO",
        "org":         "Colorado General Assembly",
    },
    {
        "type":        "direct",
        "confirmed":   False,
        "url":         "https://www.dhcd.virginia.gov/sites/default/files/Docx/landlord-tenant/landlord-tenant-handbook-final.pdf",
        "collection":  "info",
        "filename":    "virginia_landlord_tenant_handbook.pdf",
        "description": "Virginia Residential Landlord and Tenant Act Handbook (effective July 1, 2025)",
        "state":       "VA",
        "org":         "Virginia Department of Housing and Community Development",
    },
    {
        "type":        "direct",
        "confirmed":   False,
        "url":         "https://www.dhcd.virginia.gov/sites/default/files/Docx/landlord-tenant/final-vrlta-statement-formatted.pdf",
        "collection":  "info",
        "filename":    "virginia_tenant_rights_statement.pdf",
        "description": "Virginia Statement of Tenant Rights and Responsibilities",
        "state":       "VA",
        "org":         "Virginia Department of Housing and Community Development",
    },

    # ── GOLD STANDARD LEASES: Official / authoritative lease templates ─────────
    # Used by the agent to establish what fair, balanced lease language looks like.

    {
        "type":        "direct",
        "confirmed":   False,
        "url":         "https://files.santaclaracounty.gov/exjcpb1426/2025-02/sample-ca_041_lease_agreement.pdf",
        "collection":  "gold_standard_leases",
        "filename":    "santa_clara_county_ca_sample_lease_2025.pdf",
        "description": "Santa Clara County CA Official Sample Residential Lease Agreement (2025)",
        "state":       "CA",
        "org":         "Santa Clara County",
    },
    {
        "type":        "direct",
        "confirmed":   False,
        "url":         "https://nystatemls.com/documents/forms/NYStateMLS_Residential_Lease_Agreement.pdf",
        "collection":  "gold_standard_leases",
        "filename":    "ny_state_mls_residential_lease.pdf",
        "description": "New York State MLS Standard Residential Lease Agreement",
        "state":       "NY",
        "org":         "NY State MLS",
    },

    # ── OTHER LEASES: Real-world and city-specific examples ───────────────────
    # Used by the agent to compare against — includes market-rate examples
    # that may contain standard but imperfect clauses.

    {
        "type":        "direct",
        "confirmed":   True,
        "url":         "https://myplaceinchicago.com/Chicago_Residential_Lease_2024.pdf",
        "collection":  "other_leases",
        "filename":    "chicago_residential_lease_2024.pdf",
        "description": "Chicago Residential Lease Agreement 2024",
        "state":       "IL",
        "org":         "My Place in Chicago",
    },
    {
        "type":        "direct",
        "confirmed":   True,
        "url":         "https://eforms.com/images/2015/10/city-of-chicago-illinois-residential-lease-agreement-template.pdf",
        "collection":  "other_leases",
        "filename":    "chicago_model_lease_eforms.pdf",
        "description": "City of Chicago Model Apartment Lease Agreement Template",
        "state":       "IL",
        "org":         "eForms / Chicago Association of Realtors",
    },

    # ── CRAWL: Landing pages — find and download all relevant PDFs ────────────
    # These pages contain multiple linked PDFs.  The crawler filters by
    # keyword and domain so only lease/tenant-related documents are saved.

    {
        "type":         "crawl",
        "confirmed":    True,
        "url":          "https://ag.ny.gov/resources/individuals/tenants-homeowners",
        "collection":   "info",
        "description":  "NY AG Tenants and Homeowners resource page",
        "state":        "NY",
        "org":          "New York Attorney General",
        "crawl_domain": "ag.ny.gov",
    },
    {
        "type":         "crawl",
        "confirmed":    True,
        "url":          "https://www.hud.gov/program_offices/administration/hudclips/forms/hud5",
        "collection":   "gold_standard_leases",
        "description":  "HUD HUDCLIPS HUD-5xxxx federal lease and housing assistance forms",
        "state":        "federal",
        "org":          "U.S. Department of Housing and Urban Development",
        "crawl_domain": "hud.gov",
    },
    {
        "type":         "crawl",
        "confirmed":    True,
        "url":          "https://www.chicago.gov/city/en/depts/doh/provdrs/renters.html",
        "collection":   "info",
        "description":  "City of Chicago Department of Housing — For Renters page",
        "state":        "IL",
        "org":          "City of Chicago Department of Housing",
        "crawl_domain": "chicago.gov",
    },
    {
        "type":         "crawl",
        "confirmed":    True,
        "url":          "https://www.domu.com/landlord-resources/apartment-lease-forms",
        "collection":   "other_leases",
        "description":  "Domu free Chicago CRLTO-compliant apartment lease forms",
        "state":        "IL",
        "org":          "Domu",
        "crawl_domain": "domu.com",
    },

    # ── TURBOTENANT: All 50 state attorney-reviewed lease templates ───────────
    # TurboTenant embeds a WordPress attachment ID ("pdf":[ID,...]) in the HTML
    # of each state's sample page.  The PDF is then accessible at /?p={ID}
    # with no login or payment required — confirmed live for IL and NY.
    # These are landlord-platform templates that are legally reviewed and
    # state-specific, making them ideal gold-standard comparison documents.
    {
        "type":        "turbotenant",
        "confirmed":   True,
        "collection":  "gold_standard_leases",
        "org":         "TurboTenant",
        "state_slugs": [
            "alabama", "alaska", "arizona", "arkansas", "california",
            "colorado", "connecticut", "delaware", "florida", "georgia",
            "hawaii", "idaho", "illinois", "indiana", "iowa",
            "kansas", "kentucky", "louisiana", "maine", "maryland",
            "massachusetts", "michigan", "minnesota", "mississippi", "missouri",
            "montana", "nebraska", "nevada", "new-hampshire", "new-jersey",
            "new-mexico", "new-york", "north-carolina", "north-dakota", "ohio",
            "oklahoma", "oregon", "pennsylvania", "rhode-island", "south-carolina",
            "south-dakota", "tennessee", "texas", "utah", "vermont",
            "virginia", "washington", "west-virginia", "wisconsin", "wyoming",
        ],
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────────────────────────────────────

def _is_pdf(content: bytes) -> bool:
    """Return True if the raw bytes begin with the PDF magic bytes."""
    return content[:4] == b"%PDF"


def _is_relevant(href: str, link_text: str) -> bool:
    """
    Return True if a PDF link looks relevant to leases / tenant rights.
    Checks both the URL path and the visible anchor text.
    """
    haystack = (href + " " + link_text).lower()
    if any(kw in haystack for kw in EXCLUDE_KEYWORDS):
        return False
    return any(kw in haystack for kw in INCLUDE_KEYWORDS)


def _safe_filename(raw: str, state_prefix: str) -> str:
    """
    Produce a clean filename from a URL path segment.
    Prepends the state prefix to avoid cross-state filename collisions.
    """
    # Strip query string / fragment from path
    stem = Path(urlparse(raw).path).name
    stem = re.sub(r"[^\w.\-]", "_", stem)
    if not stem.lower().endswith(".pdf"):
        stem += ".pdf"
    prefix = state_prefix.lower().replace(" ", "_")
    if not stem.startswith(prefix):
        stem = f"{prefix}_{stem}"
    return stem[:120]  # keep filenames sane on all OSes


def _load_manifest() -> dict:
    """Load the existing download manifest (or start fresh)."""
    if MANIFEST_FILE.exists():
        try:
            return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_FILE.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _fetch(url: str, verbose: bool = False) -> requests.Response | None:
    """
    GET a URL and return the response object on success, None on any failure.
    Uses streaming so large files don't get buffered before size checks.
    """
    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=REQUEST_TIMEOUT, stream=True
        )
        resp.raise_for_status()
        return resp
    except requests.exceptions.HTTPError as exc:
        print(f"    ✗ HTTP {exc.response.status_code}: {url}")
        return None
    except requests.RequestException as exc:
        print(f"    ✗ Network error ({type(exc).__name__}): {url}")
        return None


def _write_pdf(
    content: bytes,
    dest_dir: Path,
    filename: str,
    manifest: dict,
    source_url: str,
    source_meta: dict,
    dry_run: bool,
    verbose: bool,
) -> bool:
    """
    Validate content is a real PDF, write it to dest_dir/filename,
    and record the download in the manifest.
    Returns True on success.
    """
    if not _is_pdf(content):
        print(f"    ✗ Response is not a valid PDF — skipping {filename}")
        return False

    size_kb = len(content) / 1024
    print(f"    ✓  {filename}  ({size_kb:.0f} KB)")

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / filename).write_bytes(content)
        manifest[filename] = {
            "filename":     filename,
            "collection":   source_meta.get("collection", ""),
            "source_url":   source_url,
            "description":  source_meta.get("description", ""),
            "state":        source_meta.get("state", ""),
            "org":          source_meta.get("org", ""),
            "size_bytes":   len(content),
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        }
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Download logic
# ─────────────────────────────────────────────────────────────────────────────

def download_direct(
    source: dict,
    manifest: dict,
    force: bool,
    dry_run: bool,
    verbose: bool,
) -> tuple[int, int]:
    """
    Download a single known PDF URL.
    Returns (files_downloaded, files_skipped).
    """
    dest_dir  = COLLECTION_DIRS[source["collection"]]
    filename  = source["filename"]
    dest_path = dest_dir / filename

    tag = "  [unconfirmed URL]" if not source.get("confirmed") else ""
    print(f"  → {source['description']}{tag}")

    if dest_path.exists() and not force:
        print(f"    ↷  Already exists — skipping  (--force to re-download)")
        return 0, 1

    resp = _fetch(source["url"], verbose)
    if resp is None:
        print(f"    ✗ Download failed — skipping")
        return 0, 0

    content = resp.content
    if len(content) > MAX_PDF_BYTES:
        if verbose:
            print(f"    ✗ File too large ({len(content) / 1024 / 1024:.1f} MB) — skipping")
        time.sleep(REQUEST_DELAY)
        return 0, 0

    saved = _write_pdf(
        content, dest_dir, filename, manifest,
        source["url"], source, dry_run, verbose,
    )
    time.sleep(REQUEST_DELAY)
    return (1, 0) if saved else (0, 0)


def crawl_page(
    source: dict,
    manifest: dict,
    force: bool,
    dry_run: bool,
    verbose: bool,
) -> tuple[int, int]:
    """
    Fetch a landing page, discover all PDF links, filter by relevance,
    and download each one.
    Returns (files_downloaded, files_skipped).
    """
    print(f"  → Crawling: {source['description']}")

    resp = _fetch(source["url"], verbose)
    if resp is None:
        return 0, 0

    soup         = BeautifulSoup(resp.text, "html.parser")
    base_url     = source["url"]
    crawl_domain = source.get("crawl_domain", "")
    dest_dir     = COLLECTION_DIRS[source["collection"]]
    state_prefix = source.get("state", "xx")

    # Collect candidate PDF links from the page
    candidates: list[tuple[str, str]] = []
    for a_tag in soup.find_all("a", href=True):
        href      = urljoin(base_url, a_tag["href"])
        link_text = a_tag.get_text(strip=True)

        if not href.lower().endswith(".pdf"):
            continue

        # Stay within the specified domain
        if crawl_domain and crawl_domain not in urlparse(href).netloc:
            continue

        if not _is_relevant(href, link_text):
            if verbose:
                print(f"    ↷  Not relevant, skipping: {Path(urlparse(href).path).name}")
            continue

        candidates.append((href, link_text))

    if not candidates:
        print(f"    ✗ No relevant PDF links found on this page")
        return 0, 0

    print(f"    Found {len(candidates)} relevant PDF link(s)")
    downloaded = skipped = 0
    time.sleep(REQUEST_DELAY)

    for href, link_text in candidates:
        filename  = _safe_filename(href, state_prefix)
        dest_path = dest_dir / filename

        if dest_path.exists() and not force:
            if verbose:
                print(f"    ↷  {filename}  (already exists)")
            skipped += 1
            continue

        if verbose:
            print(f"    Fetching: {href}")

        pdf_resp = _fetch(href, verbose)
        if pdf_resp is None:
            time.sleep(REQUEST_DELAY)
            continue

        content = pdf_resp.content
        if len(content) > MAX_PDF_BYTES:
            if verbose:
                print(f"    ✗ {filename} too large ({len(content)/1024/1024:.1f} MB) — skipping")
            time.sleep(REQUEST_DELAY)
            continue

        # Build per-file metadata for the manifest
        file_meta = {**source, "description": link_text or href}
        saved = _write_pdf(
            content, dest_dir, filename, manifest,
            href, file_meta, dry_run, verbose,
        )
        if saved:
            downloaded += 1
        time.sleep(REQUEST_DELAY)

    return downloaded, skipped


def download_turbotenant(
    source: dict,
    manifest: dict,
    force: bool,
    dry_run: bool,
    verbose: bool,
) -> tuple[int, int]:
    """
    Download attorney-reviewed lease PDFs for every state slug listed in
    source["state_slugs"] from TurboTenant.

    Strategy:
      1. Fetch the state's sample page at:
             turbotenant.com/rental-lease-agreement/sample/{slug}/
      2. Extract the WordPress PDF attachment ID from the embedded
             "pdf":[ID, ...] pattern in the page HTML.
      3. Download the PDF via:
             turbotenant.com/?p={ID}

    The PDFs are publicly accessible — no account or payment required.
    Returns (files_downloaded, files_skipped).
    """
    _PDF_ID_RE = re.compile(r'"pdf":\[(\d+)')
    _BASE_SAMPLE = "https://www.turbotenant.com/rental-lease-agreement/sample/{slug}/"
    _PDF_URL     = "https://www.turbotenant.com/?p={pdf_id}"

    dest_dir    = COLLECTION_DIRS[source["collection"]]
    state_slugs = source["state_slugs"]
    downloaded  = skipped = 0

    print(f"  → TurboTenant: fetching {len(state_slugs)} state lease templates")

    for slug in state_slugs:
        abbrev     = STATE_ABBREVIATIONS.get(slug, slug[:2].upper())
        state_name = slug.replace("-", " ").title()
        filename   = f"turbotenant_{slug.replace('-', '_')}_residential_lease.pdf"
        dest_path  = dest_dir / filename

        if dest_path.exists() and not force:
            if verbose:
                print(f"    ↷  {filename}  (already exists)")
            skipped += 1
            continue

        # Step 1 — fetch the state sample page to extract the PDF ID
        page_url  = _BASE_SAMPLE.format(slug=slug)
        page_resp = _fetch(page_url, verbose)
        if page_resp is None:
            if verbose:
                print(f"    ✗ Could not load sample page for {state_name}")
            time.sleep(REQUEST_DELAY)
            continue

        match = _PDF_ID_RE.search(page_resp.text)
        if not match:
            if verbose:
                print(f"    ✗ No PDF attachment ID found in page HTML for {state_name}")
            time.sleep(REQUEST_DELAY)
            continue

        pdf_id  = match.group(1)
        pdf_url = _PDF_URL.format(pdf_id=pdf_id)
        time.sleep(REQUEST_DELAY)

        # Step 2 — download the PDF
        pdf_resp = _fetch(pdf_url, verbose)
        if pdf_resp is None:
            time.sleep(REQUEST_DELAY)
            continue

        content = pdf_resp.content
        if len(content) > MAX_PDF_BYTES:
            if verbose:
                print(f"    ✗ {filename} too large ({len(content)/1024/1024:.1f} MB) — skipping")
            time.sleep(REQUEST_DELAY)
            continue

        file_meta = {
            **source,
            "state":       abbrev,
            "description": f"TurboTenant {state_name} Residential Lease Agreement (attorney-reviewed)",
        }
        saved = _write_pdf(
            content, dest_dir, filename, manifest,
            pdf_url, file_meta, dry_run, verbose,
        )
        if saved:
            downloaded += 1
        time.sleep(REQUEST_DELAY)

    return downloaded, skipped


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Download authoritative lease/tenant-rights PDFs into rag_data/.\n"
            "Only writes to rag_data/ — safe to run without touching the rest of the project."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be downloaded without writing any files.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download files even if they already exist on disk.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show detailed output for each HTTP request.",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("\n── DRY RUN — no files will be written ──\n")

    manifest         = _load_manifest()
    total_downloaded = 0
    total_skipped    = 0
    total_failed     = 0

    print(f"LeaseGuard AI — RAG Data Downloader")
    print(f"Writing to: {RAG_DATA_DIR.resolve()}\n")
    print(f"{'Source':<6}  {'State':<8}  Description")
    print("─" * 70)

    for i, source in enumerate(SOURCES, 1):
        state = source.get("state", "??").upper()
        print(f"\n[{i:02d}/{len(SOURCES)}]  {state:<8}  {source['org']}")

        try:
            if source["type"] == "direct":
                dl, sk = download_direct(
                    source, manifest, args.force, args.dry_run, args.verbose
                )
            elif source["type"] == "crawl":
                dl, sk = crawl_page(
                    source, manifest, args.force, args.dry_run, args.verbose
                )
            elif source["type"] == "turbotenant":
                dl, sk = download_turbotenant(
                    source, manifest, args.force, args.dry_run, args.verbose
                )
            else:
                print(f"  ✗ Unknown source type: {source['type']!r}")
                dl, sk = 0, 0

            total_downloaded += dl
            total_skipped    += sk
            if dl == 0 and sk == 0:
                total_failed += 1

        except Exception as exc:  # noqa: BLE001 — surface unexpected errors without crashing
            print(f"  ✗ Unexpected error: {exc}")
            total_failed += 1

    # ── Persist manifest ──────────────────────────────────────────────────────
    if not args.dry_run:
        _save_manifest(manifest)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "─" * 70)
    print(f"  Downloaded : {total_downloaded} new file(s)")
    print(f"  Skipped    : {total_skipped}  (already on disk)")
    print(f"  Failed     : {total_failed}  (network error or invalid PDF)")
    print("─" * 70)

    if total_downloaded > 0 and not args.dry_run:
        print(
            "\n✅  New files saved to rag_data/\n"
            "\n⚠️   NEXT STEP — rebuild the ChromaDB index:\n"
            "    Open agent/agent.ipynb and re-run the indexing cells.\n"
            "    Until you do this, the agent will NOT see the new documents.\n"
        )
        print(f"📋  Provenance log: {MANIFEST_FILE}\n")
    elif args.dry_run:
        print("\n(Dry run complete — no files written)\n")
    else:
        print("\n✅  Nothing new to download — all sources already on disk.\n")


if __name__ == "__main__":
    main()
