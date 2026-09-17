---
name: document-reader
description: Parse local documents (PDF, Office legacy+modern Word/Excel/PPT, images, text/CSV/JSON/XML/MD, HTML, ZIP, ePub) into structured plain text + page-split chunks + extracted tables + Markdown + metadata. Use when a user uploads an attachment, asks to read, summarize, translate, build RAG chunks, or extract tables from a local file path. Supports scanned-PDF OCR via the default free MinerU cloud path or the optional offline markitdown-ocr LLM-Vision plugin; defaults are safe and never trigger accidental LLM billing.
license: Proprietary. Internal use.
compatibility: Requires Python 3.12+ and uv (auto-bootstrapped on first run via the scripts/run.* entry points). Default online parse needs internet access to MinerU cloud. Optional MINERU_TOKEN unlocks Precision mode; optional LLM_API_KEY enables fully-offline OCR for scanned PDFs and embedded Office images.
metadata:
  author: internal
  version: "0.1.0"
---

# document-reader

## What it does

Reads a **local file** and normalizes many formats into one structured output, so the LLM can summarize, translate, QA, chunk for RAG, extract tables, or otherwise consume the content reliably.

## When to use

Trigger this skill **only for local files** in any of these categories:

- **PDF / Office**: `pdf`, `doc`, `docx`, `xls`, `xlsx`, `ppt`, `pptx`
- **Image**: `jpg`, `jpeg`, `png`, `bmp`, `gif`, `webp`, `tiff`, `tif`
- **Text-like**: `csv`, `json`, `xml`, `md`, `txt`, `html`, `epub`, `zip`

Typical user phrases: **"read this file"**, **"parse the PDF"**, **"summarize the attachment"**, **"extract tables from the Excel"**, **"OCR this scan"**, **"build RAG chunks from the docs"**.

## When NOT to use

- Remote URLs. Download the file locally first, then call this skill.
- Video, audio, executables, unknown binaries.

## How to invoke

**Always call through one of the two scripts.**  There is no separate install step - bootstrapping is automatic on first run.

```bash
# Windows
.\scripts\run.bat  <ABSOLUTE_FILE_PATH> [options...]

# macOS / Linux
./scripts/run.sh <ABSOLUTE_FILE_PATH> [options...]
```

### Exit codes (ACT on them - do not ignore)

| Exit | Meaning | Action |
|------|---------|--------|
| `0`  | Success. Parsed files were written to disk and stdout prints the saved folder. | Read the output folder contents; respond to the user. |
| `10` | Bootstrap blocked (network). Retry ONCE after 3 seconds. On second failure tell the user "temporary network issue, please retry in 1 minute". **Never ask the user to install Python or uv.** |
| `2`  | File not found. Stdout has `{ok:false,...}`. Ask the user to confirm the path. |
| `3`  | Parse failed. Stdout has `ok:false` + `retryable:true/false`. Retry once if `retryable==true` (network blip / rate limit). Otherwise surface the message directly. |
| `4`  | Unexpected error. Retry once; if still fails, surface the error text. |

### Input flags (map user intent to these)

Defaults are safe - pass no flags for a normal default parse.

| User says                                                                 | Flags to pass |
|---------------------------------------------------------------------------|---------------|
| "read this file"                                                          | (none) |
| "fast parse" / "skip cloud" / "I just need the raw text quickly"         | `--fast`  (local-only; skips MinerU entirely; best for text-embedded PDFs, Office, plain text) |
| "only PDF pages 1-20"                                                     | `--pages 1-20` |
| "OCR this scanned PDF / recognize text in this image"                     | `--ocr`  (recommended; uses MinerU cloud OCR, free for normal use.  Ignored when `--fast` is on) |
| "encrypted PDF, password is abc123"                                       | `--password abc123` |
| "file has no extension / treat it as docx"                                | `--file-type-hint docx`  (or `pdf`, `xlsx`, `png`, `csv`, ...) |
| "force fast MinerU mode" / "force high-quality MinerU mode"               | `--mineru-mode flash`  /  `--mineru-mode precision`  (precision requires `MINERU_TOKEN`.  Ignored when `--fast` is on) |
| "show just plain text" / "show just markdown" / "short preview"           | `--output-format text --chars 4000`  /  `--output-format markdown --chars 8000`  /  `--output-format preview --chars 2000` |
| "this file is 1.2 GB, I know what I'm doing"                              | `--max-file-size-mb 1500` |
| "save results to a specific folder"                                       | `-o ./custom_output`  or  `--output-dir ./custom_output` |
| "fully offline scanned PDF OCR" (MinerU unreachable + LLM key available)  | `--enable-ocr-plugin`  (WARNING: consumes LLM tokens; only use when the user explicitly agrees) |

## Output contract

On **exit 0** the skill creates a folder and prints one line to stdout:

```
Saved to: <ABSOLUTE_OUTPUT_DIR>
```

### Output folder layout

```
<output-dir>/                    # default: ./output/
└── <source-file-basename>/      # one folder per input file
    ├── result.json              # structured metadata (4 fields only; see table below)
    ├── full.md                  # markdown output; always present; SDK native filename
    ├── images/                  # [MinerU only] extracted figures / photos
    ├── layout.json              # [MinerU only] page layout coordinates
    ├── *_content_list.json      # [MinerU only] structured block list
    ├── *_model.json             # [MinerU only] model-level metadata
    ├── full.docx                # [MinerU extra_formats] Word export
    ├── full.html                # [MinerU extra_formats] HTML export
    ├── full.tex                 # [MinerU extra_formats] LaTeX export
    └── *_origin.pdf             # [MinerU only] original input copy
```

`result.json` and `full.md` are always present.  The additional files (`images/`, `layout.json`, `*_content_list.json`, `*_model.json`, `full.docx`, `full.html`, `full.tex`, `*_origin.pdf`) are produced only when `method` starts with `mineru-open-sdk[precision]` and are optional for consumers.

### Fields inside `result.json`

| Field         | Type                         | Purpose |
|---------------|------------------------------|---------|
| `file_path`   | string                       | Absolute path of the source file |
| `file_type`   | string (e.g. `pdf`, `docx`)  | Detected / hinted format |
| `method`      | string                       | Final parser that produced the output: `mineru-open-sdk[precision]`, `mineru-open-sdk[flash]`, `markitdown`, `markitdown-ocr`, `pypdf`, `text_parser`, etc. |
| `metadata`    | `dict`                       | Size, mtime, SDK mode, scanned hint, and any parser-provided tags |

Full extracted content lives in the sibling `full.md` file next to this JSON.  For table extraction, page-level chunking, or raw plain text, read the content directly from `full.md` and parse to your needs.

## Important rules

1. **Never bypass `scripts/run.*`.**  Direct Python imports or `pip install` instructions are NEVER part of the user-facing flow.
2. **Defaults prevent billing surprises.**  The default path calls MinerU cloud (free for normal use) and never invokes an LLM.  Only pass `--enable-ocr-plugin` when: (a) the user explicitly needs offline scanned-PDF OCR, (b) MinerU cloud is unreachable, (c) an LLM key is configured.
3. **Empty scanned PDF?**  If `len(text)` from `result.json` is small, retry **once** with `--ocr` (MinerU cloud path) first.  Only fall back to `--enable-ocr-plugin` if the user is truly offline.
4. **Legacy `.doc` / `.xls` / `.ppt`.**  If the parse looks garbled, suggest converting via LibreOffice first (`soffice --convert-to {docx,xlsx,pptx} <file>`) and re-running.
