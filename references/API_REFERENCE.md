# Document Reader Skill - Public API Reference

This file contains the detailed Python Public API.  Agents normally call
the skill through the `scripts/run.bat` / `scripts/run.sh` CLI entry
points; this reference exists for writing tests, scripts, or advanced
batch jobs that need the Python facade directly.

## 1. Package exports (`from document_reader import ...`)

Three public names are re-exported from `src/__init__.py`:

```python
from document_reader import (
    DocumentReader,     # Facade class - the main entry point
    ParsedDocument,     # @dataclass - normalized output contract
    DocumentParseError, # Typed exception raised on parse failures
)
```

## 2. `DocumentReader` facade class

### Constructor

```python
class DocumentReader:
    def __init__(
        # --- mineru-open-sdk (Level 1 online parser) ---
        prefer_mineru_sdk: bool = True,
        mineru_token: str | None = None,
        mineru_ocr: bool | None = None,
        mineru_language: str = "ch",
        mineru_model: str | None = None,
        mineru_formula: bool = True,
        mineru_table: bool = True,
        mineru_timeout: float | None = None,
        mineru_base_url: str | None = None,
        # --- markitdown-ocr plugin (opt-in offline OCR) ---
        enable_markitdown_ocr: bool = False,
        llm_client: Any | None = None,
        llm_model: str | None = None,
        llm_prompt: str | None = None,
        # --- general knobs ---
        max_file_size_mb: int = 500,
        python_version_strict: bool = True,
    ) -> None:
```

### Public methods

| Method | Signature | Purpose |
|---|---|---|
| `.read(...)` | `read(file_path: str, *, file_type_hint: str \| None = None, password: str \| None = None, pages: str \| None = None, is_ocr: bool \| None = None, **extra: Any) -> ParsedDocument` | Parse one file. This is the method that implements the 7-way dispatch by file type and walks the cascade.  `password` is forwarded to `pypdf.decrypt(...)` for encrypted PDFs.  `pages` is a string `"1-20"` or `"3,5,7-9"` passed down to the MinerU SDK parser or pypdf.  `**extra` is forwarded transparently to the winning parser. |
| `.read_batch(...)` | `read_batch(file_paths: list[str], *, fail_fast: bool = False, **read_kwargs: Any) -> list[ParsedDocument \| DocumentParseError]` | Parse a list of files.  `fail_fast=False` (default) replaces bad positions with the exception object - a batch of 100 files with 1 bad file still returns 100 entries.  `fail_fast=True` raises on the first failure.  `**read_kwargs` are forwarded to every `.read(...)` call. |
| `.capability()` | `capability() -> dict[str, bool]` | Returns a snapshot dict describing which optional features currently work, e.g. `python_312_plus`, `network_available`, `mineru_sdk_installed`, `markitdown_installed`, `markitdown_ocr_installed`, `python_magic_installed`.  Useful for logging and for deciding which cascade level will actually run before calling `.read(...)`. |

## 3. `ParsedDocument` output contract

```python
@dataclass
class ParsedDocument:
    file_path: str
    file_type: str
    text: str = ""
    pages: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    markdown: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    parser_used: str = ""
```

| Field | Type | Semantics |
|---|---|---|
| `file_path` | `str` | Absolute resolved path after `Path(...).resolve()`.  Always populated. |
| `file_type` | `str` | Final detected label, e.g. `"pdf"`, `"docx"`, `"png"`, `"csv"`, `"json"`, `"unknown"`. |
| `text` | `str` | Best plain text.  If `.markdown` was produced by a parser, `.text` is a de-markdown'd view of it so plain-text prompts never contain `## Headers` or `| table | pipes |`. |
| `pages` | `list[str]` | Page- or chunk-split text for reproducible citations.  MinerU SDK fills this from `content_list` with real PDF page boundaries; markitdown / pypdf / text parsers fall back to heuristic chunking. |
| `tables` | `list[list[list[str]]]` | Three-dimensional array: outer = tables, middle = rows, inner = cells.  Each table's first row is the header row when available, so callers can do `pd.DataFrame(columns=tbl[0], data=tbl[1:])`.  Always a list; empty `[]` if no tables were found.  Populated by MinerU content_list (`type=="table"` rows), by markitdown markdown-table reverse-parsing regex, or by pandas for CSV / Excel files. |
| `markdown` | `str` | Best structured markdown output.  **Always populated for MinerU and markitdown branches.**  This is the preferred field for LLM prompts that do summarization, QA, translation, or chunking into RAG. |
| `metadata` | `dict[str, Any]` | Arbitrary provenance: `size`, `mtime`, `detected_language`, `pages_count`, `sdk_mode` (`flash` / `precision`), `scanned_pdf_hint`, `ocr_hint`, `decrypt_result`, `image_width`, `image_height`, EXIF tags.  Callers should treat values as optional. |
| `parser_used` | `str` | Human-readable chain that actually ran.  Examples: `"mineru-open-sdk[precision]"`, `"mineru-open-sdk(failed ConnectTimeout) -> markitdown"`, `"markitdown(empty_text<50, markitdown-ocr retry) -> pypdf"`, `"text[csv, pandas]"`.  This is the first place to look when debugging a surprising result. |

### Convenience helpers

| Helper | Returns | Purpose |
|---|---|---|
| `.to_dict()` | `dict[str, Any]` | `dataclasses.asdict(self)`.  JSON-serializable. |
| `.to_json(indent=2, ensure_ascii=False)` | `str` | UTF-8 JSON dump.  Chinese characters stay as-is instead of `\uXXXX`. |
| `.preview_chars(n=500)` | `str` | Truncated preview of `.text` / `.markdown` for logging or quick previews in chat.  Suffixes with `"...<truncated>"` if it was cut. |
| `repr(doc)` | `str` | Short one-line preview including lengths of `text`, `pages`, `tables`, and a 120-char preview. |

## 4. `DocumentParseError` exception

```python
class DocumentParseError(Exception):
    def __init__(self, message: str, *, original_error: BaseException | None = None, retryable: bool = False) -> None:
        ...
```

| Attribute | Type | Meaning |
|---|---|---|
| `.message` (inherited from `str(...)`) | `str` | Human-readable error. |
| `.original_error` | `BaseException \| None` | The original exception if wrapping.  Useful for logging. |
| `.retryable` | `bool` | `True` when the error is transient (network, rate limit, 5xx) and the caller can retry immediately.  **The facade uses this to decide whether to fall through to the next cascade level.**  Non-retryable (corrupt file / wrong password / file too large) propagates immediately. |
