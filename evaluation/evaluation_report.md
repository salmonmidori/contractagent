# Lease Analysis Agent — Evaluation Report

**Date:** 2026-02-20  
**Agent output evaluated:** `clause_findings.csv` + `improvement_decision_report.md`  
**Ground truth source:** `ground_truth/ground_truth.json` (17 clause types)

---

## Executive Summary

The agent's analysis is largely accurate and well-reasoned, identifying key issues related to tenant rights and privacy. However, there are some gaps in completeness and specificity in recommendations that could enhance the overall quality of the evaluation.

---

## Evaluation Metrics

| Metric | Score | Method |
|--------|-------|--------|
| Coverage Rate | 5.9% | LLM matching |
| Label Accuracy | 0.0% | LLM matching |
| Accuracy | 80.0% | LLM-as-judge |
| Completeness | 70.0% | LLM-as-judge |
| Severity Calibration | 80.0% | LLM-as-judge |
| Recommendation Quality | 70.0% | LLM-as-judge |
| Legal Accuracy | 90.0% | LLM-as-judge |
| **Overall Score** | **78.0%** | Weighted avg |

*See `evaluation_results.png` for a visual summary.*

---

## Strengths

- The agent correctly identifies several problematic areas related to tenant privacy and due process.
- Legal references and reasoning are generally accurate and well-supported.
- The analysis provides a clear structure and logical flow, making it easy to follow.

## Weaknesses

- Some issues are labeled as 'fair' when they could be more accurately described as needing negotiation or improvement.
- The analysis lacks specific recommendations for addressing privacy concerns related to inspections.
- The severity scores for some clauses may not fully reflect the potential risks involved.

## Missed Issues

Issues present in the ground truth that the agent did not identify:

- Potential implications of vague language regarding pest treatment responsibilities.
- Specific state laws regarding security deposits and late fees were not addressed.

## False Positives

Issues the agent flagged that were not genuinely problematic:

- (none identified)

---

## Coverage Detail

| Ground Truth Clause | Found? | Match Quality | Agent Finding | Label Match | Severity Match | Notes |
|---------------------|--------|--------------|---------------|-------------|----------------|-------|
| Waiver of Habitability Rights | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Eviction Without Judicial Process | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Acceleration of Rent Payments | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Tenant Pays Landlord's Attorney Fees | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Confession of Judgment Clause | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Landlord Liability Exemption | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Excessive Security Deposit Requirements | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Discriminatory Lease Terms | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Unreasonable Late Fees | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Prohibition of Guests or Roommates | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Waiver of Right to Legal Action | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Restriction on Emergency Services | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Denial of Security Deposit Receipt | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Rent Increase Notification | ✓ | partial | Movein/Initial Representations About Bed Bugs (The Inspection/Disclosure Component) | incorrect | too_low | Agent identified a clause but it does not match the ground truth. |
| Restrictions on Subletting | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Lease Termination Notice Requirements | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |
| Tenant Maintenance Responsibilities | ✗ | not_found | — | not_applicable | not_available | No relevant clause identified. |

---

## Methodology Notes

### Ground Truth Construction
The ground truth was built using two approaches:
1. **Reference PDFs**: Text was extracted from `rag_data/info/` documents ("10 Deadly Sins of a Lease", 
   "How to Identify and Fix Illegal Lease Clauses") using `pypdf`.
2. **Web scraping**: Public tenant-rights pages from NOLO, HUD, and the Illinois Attorney General were
   scraped using `requests` + `BeautifulSoup4`.
3. **LLM synthesis**: `gpt-4o-mini` was used to synthesize both sources into a structured JSON list of 
   clause types with expected labels, severity ranges, and key indicators.

### Evaluation Approach
Exact string matching is not suitable for legal text analysis (paraphrasing, different clause names, etc.).
Instead, two LLM-based evaluation methods were used:

1. **LLM Coverage Matching** (`gpt-4o-mini`): Matched each ground truth clause type to the agent's 
   findings, assessing whether each issue was identified and correctly labeled.
2. **LLM-as-Judge** (`gpt-4o`): Scored the analysis quality on five dimensions (Accuracy, Completeness, 
   Severity Calibration, Recommendation Quality, Legal Accuracy) on a 0-10 scale.

### Limitations
- LLM-based evaluation is itself subject to model bias and limitations.
- Ground truth reflects the reference documents used; other authoritative sources may yield different entries.
- Evaluation was performed on a single lease agreement (Chicago Bed Bug Addendum). Results may vary 
  across different lease types and jurisdictions.

---
*Generated by `02_evaluate_output.ipynb`*
