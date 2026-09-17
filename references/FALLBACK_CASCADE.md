# Parser Fallback Cascade

This document is a deep reference for the skill's parser selection and
degradation order.  Agents should NOT change this cascade - call the skill
and it will auto-select.  Use this file only when debugging failures or
when explaining to a user why a given `parser_used` string was returned.

## 1. Global selection rules

The top-level `DocumentReader` facade decides:

1.  Resolve the absolute file path.
2.  Detect the file type via `detect_file_type()` - extension first,
    optional `python-magic` MIME second, user hint overrides all.
3.  Validate the file against `max_file_size_mb` (default 500 MB) and
    the Python interpreter version (strict mode requires >=3.12).
4.  Route to the correct format-specific parser.  The parser then walks
    its own cascade.

Any retryable error (network timeout, 429 rate limit, 5xx from MinerU,
import failure of an optional dependency) falls to the next level
**immediately** and records the transition in the `parser_used` string.

## 2. Format-by-format cascade

| Format | Level 1 (MinerU cloud, ALWAYS first when mineru-open-sdk installed) | Level 2 | Level 3 |
|---|---|---|---|
| **PDF** | `mineru-open-sdk` - Mode decided **purely by token presence**: `MINERU_TOKEN` present → `precision` via `extract()`; no token → `flash` via `flash_extract()`.  User can override with `--mineru-mode flash|precision`.  Built-in OCR, table, formula switches via SDK params.  Any error (network / auth / empty output) falls through to Level 2 immediately. | `markitdown[all]` with `plugins=False` (lightweight, no LLM).  If `len(text) < 50` AND `enable_markitdown_ocr=True` AND `llm_client` is provided, **retry once with `plugins=True`** to run the `markitdown-ocr` LLM Vision plugin against embedded page images. | `pypdf` local text extraction.  Supports `decrypt(password)` for encrypted PDFs - missing password raises a clear `DocumentParseError("PDF is encrypted, provide password")`.  If all 3 levels end with `len(text) < 50`, the result returns `metadata['scanned_pdf_hint']` listing remedies (MINERU_TOKEN or markitdown-ocr). |
| **Office (doc/docx/xls/xlsx/ppt/pptx)** | `mineru-open-sdk` - Same token→mode rule (precision if token, flash if none).  Precision mode supports legacy binary `.doc`/`.xls` natively.  OCR / table switches inherited from SDK.  Any error → Level 2. | `markitdown[all]` (best coverage for modern `.docx/.xlsx/.pptx`).  `.xlsx` additionally passes through `pandas.read_excel(...)` to strengthen the `.tables` array.  Legacy `.doc/.xls/.ppt` fallback here is best-effort. | N/A.  If markitdown also fails on a legacy binary file, raise `DocumentParseError` and suggest `soffice --convert-to {docx,xlsx,pptx} <file>` via LibreOffice as a pre-step. |
| **Images (jpg/jpeg/png/bmp/gif/webp/tiff/tif)** | `mineru-open-sdk` image input + cloud `is_ocr` switch.  Same token→mode rule (precision if token, flash if none).  EXIF, width/height metadata from Pillow always attached.  Any error → Level 2. | `markitdown[all]` with an `llm_client` passed to the wrapper constructor.  **Standalone image OCR does NOT require the `markitdown-ocr` plugin.**  When no `llm_client` is available, return an empty `.text/.markdown` plus a textual hint in `metadata['ocr_hint']` explaining how to enable OCR (provide an LLM client or run through MinerU cloud). | N/A. |
| **Text-like (txt/md/csv/json/xml/html) + unknown** | Purely local, never sent to MinerU cloud.  `chardet` multi-encoding decode, ordered fallback `utf-8-sig -> utf-8 -> gbk -> gb18030 -> latin-1`.  `csv` -> `pandas` + markdown table render.  `json` and `xml` are pretty-printed into `.markdown` with code fences for easy LLM prompting.  Unknown extensions: if a valid text decode succeeds, route here; otherwise fall back to `markitdown[all]` as a catch-all if available. | N/A. | N/A. |
| **HTML / ZIP / EPUB / MHTML** | Route directly to `markitdown[all]` first; `zip` is unpacked internally by markitdown's converter; epub is read via markitdown's reader.  Fall back to binary-file error if markitdown cannot handle. | N/A. | N/A. |

## 3. OCR policy (must match user cost expectations)

Two OCR paths exist - zero overlap with `pytesseract` or system Tesseract:

| OCR path | Trigger | Cost | Requirement |
|---|---|---|---|
| MinerU cloud OCR (default / recommended) | User passes `--ocr` or the file is an image / scanned PDF and they said "recognize the text". | Free for normal use, MINERU_TOKEN unlocks bigger files. | `mineru-open-sdk` installed + network available.  Passed via SDK param `is_ocr` (Flash) or `ocr` (Precision). |
| `markitdown-ocr` LLM-Vision plugin (opt-in offline path) | User explicitly says "I am offline / do not use MinerU / need local scan OCR" AND the target is a scanned PDF / images **embedded inside Office files.**  (Standalone images handled by markitdown[all] + llm_client alone.) | Consumes LLM tokens; user must accept the cost explicitly. | `enable_markitdown_ocr=True` at construction, `llm_client` is provided, and the `markitdown-ocr` plugin is installed via `[ocr]` extra.  `MarkItDownWrapper` is instantiated twice: once with `enable_plugins=False` (default text) and once with `enable_plugins=True` only for the scanned retry. |

**Cost safety guardrails hard-coded into the facade:**

- `enable_markitdown_ocr` defaults to `False`.  Agents MUST NOT turn it
  on unless the user explicitly opted in.
- `llm_client` is never constructed internally.  The caller (or the
  `scripts/run.* --llm-api-key` flag) must pass it in explicitly.
- The default online path (`--ocr`) routes to MinerU cloud and does NOT
  consume any user LLM tokens.

## 4. Error classification

All parser-level exceptions are re-raised as `DocumentParseError` with:

- `.message`: user-friendly explanation
- `.original_error`: the original exception object
- `.retryable`: `True` only for transient failures (network timeout, 429, 5xx, MinerU rate limit, OOM of an optional SDK)

The facade uses `.retryable` to decide whether to fall through to the
next cascade level.  Non-retryable errors (corrupt PDF bytes, unknown
file format, missing password, file-size exceeded) abort immediately.
