---
name: document-reader
description: Parse local documents (PDF, Office legacy+modern Word/Excel/PPT, images, text/CSV/JSON/XML/MD, HTML, ZIP, ePub) into structured plain text + page-split chunks + extracted tables + Markdown + metadata. Use when a user uploads an attachment, asks to read, summarize, translate, build RAG chunks, or extract tables from a local file path. Supports scanned-PDF OCR via the default free MinerU cloud path or the optional offline markitdown-ocr LLM-Vision plugin; defaults are safe and never trigger accidental LLM billing.
license: Proprietary. Internal use.
compatibility: Requires Python 3.12+ and uv (auto-bootstrapped on first run via the scripts/run.* entry points). Default online parse needs internet access to MinerU cloud. Optional MINERU_TOKEN unlocks Precision mode; optional LLM_API_KEY enables fully-offline OCR for scanned PDFs and embedded Office images.
metadata:
  author: internal
  version: "0.1.0"
---

# Document Reader Skill

A code-style agent skill that normalizes many document formats into a single
structured `ParsedDocument` output contract for downstream LLM tasks (QA,
summarization, RAG, table extraction, translation).

The skill MUST be invoked through its CLI entry point.  Never instruct the
user to install anything by hand - bootstrapping is handled transparently.

---

## Scope

**Use this skill when** the agent needs to extract structured content from:

- A local file the user uploaded, dropped in the repo, or pointed to by an
  absolute path.
- PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, common images (jpg/jpeg/png/bmp/gif/
  webp/tiff/tif), CSV/JSON/XML/Markdown/plain text, HTML, ZIP, ePub.
- Cases where the user says "read", "parse", "summarize", "extract tables",
  "build RAG", or "OCR this scan" for one or more file paths.

**Fallback / out of scope:**

- Remote URLs.  Download the file locally first, then call this skill.
- Video, executables, unknown binaries.  The skill will raise a clear
  `DocumentParseError` and suggest a conversion step.

---

## Prerequisites - TRUE ZERO CONFIG

**The user NEVER manually installs Python, uv, or `pip install` anything.**
The skill ships with two thin self-bootstrapping entry points in
[scripts/](scripts/) that download uv (a single-file binary) on first
invocation, then resolve CPython 3.12 and all pinned dependencies from
`uv.lock`.  Everything stays local to the skill folder and is covered by
`.gitignore`.

| OS | Entry point (the only command you will ever need) |
|----|--------------------------------------------------|
| Windows       | `.\scripts\run.bat  <ABSOLUTE_FILE_PATH> [options...]` |
| macOS / Linux | `chmod +x ./scripts/run.sh && ./scripts/run.sh <ABSOLUTE_FILE_PATH> [options...]` |

Optional knobs the user may explicitly provide:

- `MINERU_TOKEN` from <https://mineru.net/apiManage/token> - unlocks MinerU
  Precision mode (<=200 MB / <=200 pages).  Flash mode works without a
  token for small files (<=10 MB / <=20 pages).
- `LLM_API_KEY` (or compatible) - ONLY needed when the user opts into the
  offline `markitdown-ocr` plugin for scanned PDFs / embedded Office images
  AND cannot reach MinerU cloud.  The default path never calls any LLM.

Detailed Python facade API and parameter listings live in
[references/API_REFERENCE.md](references/API_REFERENCE.md).

---

## Workflow - 3 steps (MANDATORY order)

### Step 0 - Resolve absolute file path

If the user provided a relative path, file upload, `~`, or environment
variables, expand to an absolute path **before** calling `scripts/run.*`.
Always prefer absolute paths.  Never shell-escape by hand - let the
subprocess layer handle quoting.

### Step 1 - Invoke the run script (SINGLE entry point; bootstraps on first call)

Pick one command based on the detected OS.  Map the user's natural-
language request to CLI flags using the Inputs table below.  This step is
**idempotent and safe to re-run:**

```bash
# Windows (any shell)
.\scripts\run.bat "C:\abs\path\to\file.pdf" [--pages 1-20] [--ocr] [--password xxx] [OPTIONS...]

# macOS / Linux
chmod +x ./scripts/run.sh && ./scripts/run.sh "/abs/path/to/file.pdf" [--pages 1-20] [--ocr] [--password xxx] [OPTIONS...]
```

You do NOT need a separate install step.  Even on a completely clean
machine with zero Python / uv installed, the first invocation of
`scripts/run.*` performs bootstrap internally.  Subsequent invocations
are instant.

**Exit-code handling (MANDATORY - do not ignore exit codes):**

| Exit | Meaning | Action |
|------|---------|--------|
| `0`  | Success.  Stdout contains the JSON payload when `--output-format json` (the default).  Parse it and go to Step 2. |
| `10` | uv self-bootstrap failed (Astral / GitHub blocked).  **Do NOT ask the user to install anything.**  Retry ONCE after a 3-second wait.  On second failure just surface "temporary network issue, please retry in 1 minute" in the user's natural language. |
| `2`  | File not found.  Stdout JSON has `{"ok":false, ...}`.  Ask the user to confirm the absolute path. |
| `3`  | `DocumentParseError`.  Stdout JSON has `ok:false` + `retryable:true/false`.  Retry ONCE if `retryable==true` (network blip / rate limit).  Otherwise surface the message directly. |
| `4`  | Unexpected Python exception.  Retry ONCE; if still fails, surface the error text. |

The fallback cascade parser selection order and all OCR policies are
documented in [references/FALLBACK_CASCADE.md](references/FALLBACK_CASCADE.md).
Agents must not change the cascade.

### Step 2 - Parse the returned JSON payload and respond to the user

The default `--output-format json` prints `ParsedDocument.to_dict()`:

- LLM summarization / QA prompts: prefer `.markdown` (keeps headings,
  tables, lists), fall back to `.text`.
- RAG chunking: prefer `.pages` (stable page splits for citations), then
  `.markdown`.
- Numerical / CSV work: use `.tables` directly.  Each table is
  `list[list[str]]` with the header row first - convert to
  `pd.DataFrame(columns=tbl[0], data=tbl[1:])`.
- Provenance / troubleshooting: log `.parser_used` (shows which cascade
  actually ran) and `.metadata` (size, pages, OCR hint, SDK mode, etc).

Full field-by-field definitions live in
[references/API_REFERENCE.md](references/API_REFERENCE.md).

### Step 3 - For batch requests

Do NOT shell-loop `scripts/run.*` for 100+ files.  For batches:

1.  Call `scripts/run.*` once per file sequentially (or parallel for
    independent files).
2.  Collect stdout JSON; failed positions return `exit 2/3/4` with
    `{"ok":false,...}` on stdout - NEVER abort the whole batch because
    of one bad file.
3.  Aggregate and present successes + failures clearly.

Large batches (>50) can use the internal `DocumentReader.read_batch(...)`
facade directly from a short Python script - see the API reference.  This
is an advanced optimization, not the default path.

---

## Inputs - `scripts/run.*` CLI flags

Map the user's natural-language request onto these flags.  They all have
safe defaults: no flags means "default online parse, JSON output".

| Natural-language user request                         | Flags to pass |
|-------------------------------------------------------|---------------|
| Plain "read this file"                                | (none) - default `--output-format json` |
| "read this PDF pages 1-20 only"                       | `--pages 1-20` |
| "read scanned PDF / recognize this image / OCR this"  | `--ocr` - MinerU cloud OCR (free, recommended).  Only fall back to `--enable-ocr-plugin` when **offline + scanned PDF + LLM key exists**. |
| "encrypted PDF, password is abc123"                   | `--password abc123` |
| "file has no extension / override the file type"      | `--file-type-hint docx` (or pdf / xlsx / pptx / png / csv / json / ...) |
| "force big-file MinerU precision mode"                | `--mineru-mode precision` (requires `MINERU_TOKEN`) |
| "force small-file MinerU flash mode"                  | `--mineru-mode flash` |
| "I am offline / skip MinerU cloud"                    | None - the cascade degrades automatically.  Explicitly opt into `--enable-ocr-plugin` only if scanned PDF/Office OCR is needed and you will cover LLM image costs. |
| "show only plain text, not the full JSON"             | `--output-format text --chars 4000` |
| "show markdown only"                                  | `--output-format markdown --chars 8000` |
| "give me a short preview / what's in this file"       | `--output-format preview --chars 2000` |
| "this file is 1.2 GB and I know what I'm doing"       | `--max-file-size-mb 1500` |

---

## Validation

The entry scripts self-validate on first invocation (they run
`uv sync` internally).  To smoke-test manually without parsing a real file:

```bash
# Windows
.\scripts\run.bat --help         # prints full CLI help / options list

# macOS / Linux
chmod +x ./scripts/run.sh && ./scripts/run.sh --help
```

Expected behavior:

- Exit code 0.
- Prints a help banner listing every flag in the Inputs table.
- On a clean machine the first `--help` run is the slowest (it installs
  uv + CPython 3.12 + all pinned deps).  Every subsequent run is instant.

Run the offline unit test suite (optional):

```bash
uv run pytest tests/ -v
```

- Offline unit tests pass without any env vars or network access.
- Integration tests (markitdown / mineru SDK) auto-skip when those
  packages / network are unavailable - they never turn the suite red.

Run the official spec validator (optional; requires the `skills-ref`
reference library):

```bash
skills-ref validate .
```

---

## Gotchas

- **Never tell the user to install Python, uv, or `pip install` anything.**
  `scripts/run.*` is the ONLY entry point and does everything.  If
  bootstrap returns exit 10 twice, just say "please try again in 1
  minute" and do NOT print installation instructions.
- **Scanned PDF empty text.**  The most common user report.  If the
  returned JSON shows `len(text) < 50`, re-invoke with `--ocr` (the
  free MinerU cloud path) or - only when truly offline and a scanned
  PDF - with `--enable-ocr-plugin` (requires LLM key).
- **`markitdown-ocr` != standalone image OCR.**  Standalone images are
  handled by `markitdown[all]` + an LLM client; the `markitdown-ocr`
  plugin only adds OCR for images **embedded inside PDFs / DOCX / PPTX
  / XLSX**.  Using the plugin unnecessarily wastes LLM tokens.
- **No pytesseract / system Tesseract anywhere.**  This is intentional
  user policy.  If any error stack mentions Tesseract the environment
  is wrong.
- **Python <3.12 causes mineru-open-sdk / markitdown import errors.**
  The entry script uses `.python-version=3.12` + `uv sync` which
  guarantees the correct interpreter - never bypass the script.
- **Paths with Chinese characters or spaces.**  All internal calls use
  `Path(path).resolve()`.  Rare C-extension bugs in third-party libs
  still happen; as a workaround copy the file to an ASCII-only path.
- **Legacy `.doc` / `.xls` / `.ppt`.**  MinerU Precision supports them;
  local markitdown coverage is spotty.  In a pure-local environment,
  suggest `soffice --convert-to {docx,xlsx,pptx} <file>` first.
- **Cost controls for markitdown-ocr / LLM image descriptions.**
  `--enable-ocr-plugin` is **off by default**.  Never enable it unless
  the user explicitly asked for LLM-powered OCR and understands billing.
  `--ocr` (MinerU cloud) is the default path and free for normal use.
- **MinerU rate-limit / service outage.**  The first
  `DocumentParseError` with `retryable=True` is normal; the fallback
  cascade auto-degrades.  Do not loop-retry the online branch - it
  wastes time.
