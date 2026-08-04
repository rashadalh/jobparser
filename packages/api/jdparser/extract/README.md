# `extract/` — turning a job URL into JD text

Given a posting's redirect URL, recover the full job description as evidence. Owning spec:
[`docs/IMPLEMENTATION_EXTRACT.md`](../../../../docs/IMPLEMENTATION_EXTRACT.md).

| Module | What it holds |
|---|---|
| `resolve.py` | Follow the redirect chain to the employer's real URL (HEAD, falling back to GET). |
| `fetch.py` | Static httpx first; escalate to Playwright when the HTML is thin or JS-gated; one headed retry when a render looks blocked. |
| `jsonld.py` | schema.org `JobPosting.description` — the preferred source. |
| `ats.py` | Per-ATS CSS selectors. Data-driven: adding an ATS is one row. |
| `readable.py` | trafilatura fallback for generic pages. |
| `quality.py` | The gate: too short (`JD_TOO_SHORT`) or too much boilerplate (`JD_NOISY`). |
| `__init__.py` | `extract_jd_text` — tries JSON-LD → ATS → readable, first non-empty wins. That order is a locked decision (SPEC §10). |

Extraction failing is normal and expected; it is never fatal. A job that yields no usable
JD is reported in the audit panel, never shown as a match.
