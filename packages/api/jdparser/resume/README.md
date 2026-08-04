# `resume/` — resume file → text

`extract_text.py` turns a PDF/DOCX/TXT upload into normalized text: one extractor per
type, then whitespace/BOM normalization, then a `MIN_RESUME_CHARS` floor.

No OCR. A scanned or image-only PDF yields near-empty text and surfaces as
`RESUME_EMPTY_TEXT` rather than silently producing a garbage profile. Owning spec:
[`docs/IMPLEMENTATION_CACHE.md`](../../../../docs/IMPLEMENTATION_CACHE.md).
