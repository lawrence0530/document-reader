from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ..models.document import ParsedDocument
from .base_parser import (
    DocumentParseError,
    _file_meta,
    _safe_file_size_check,
)

log = logging.getLogger(__name__)

_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_MD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def extract_markdown_tables(md_text: str) -> list[list[list[str]]]:
    tables: list[list[list[str]]] = []
    if not md_text:
        return tables
    lines = md_text.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        if not _MD_TABLE_ROW.match(lines[i]):
            i += 1
            continue
        start = i
        while i < n and _MD_TABLE_ROW.match(lines[i]):
            i += 1
        block = lines[start:i]
        if len(block) < 2:
            continue
        sep_idx = 1 if len(block) >= 2 and _MD_TABLE_SEP.match(block[1]) else -1
        if sep_idx < 0:
            continue
        data_rows = [block[0], *block[2:]]
        table_rows: list[list[str]] = []
        for row in data_rows:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            table_rows.append(cells)
        if len(table_rows) >= 1 and any(any(c for c in row) for row in table_rows):
            tables.append(table_rows)
    return tables


class MarkItDownWrapper:
    def __init__(
        self,
        *,
        enable_plugins: bool = False,
        llm_client: Any | None = None,
        llm_model: str | None = None,
        llm_prompt: str | None = None,
    ) -> None:
        self.enable_plugins = bool(enable_plugins)
        self.llm_client = llm_client
        self.llm_model = llm_model
        self.llm_prompt = llm_prompt
        self._md: Any = None

    def _get_instance(self):
        if self._md is not None:
            return self._md
        try:
            from markitdown import MarkItDown
        except ImportError as e:
            raise DocumentParseError(
                "markitdown not installed; pip install 'markitdown[all]'",
                original_error=e,
                retryable=False,
            ) from e
        kwargs: dict[str, Any] = {
            "enable_plugins": self.enable_plugins,
        }
        if self.llm_client is not None:
            kwargs["llm_client"] = self.llm_client
            if self.llm_model:
                kwargs["llm_model"] = self.llm_model
            if self.llm_prompt:
                kwargs["llm_prompt"] = self.llm_prompt
        try:
            self._md = MarkItDown(**kwargs)
        except Exception as e:
            raise DocumentParseError(
                f"Failed to initialize MarkItDown: {e}",
                original_error=e,
                retryable=False,
            ) from e
        return self._md

    def parse(
        self,
        file_path: str | Path,
        file_type: str = "pdf",
        max_file_size_mb: int = 500,
        **extra: Any,
    ) -> ParsedDocument:
        path = Path(file_path).resolve()
        if not path.exists():
            raise DocumentParseError(f"File not found: {path}")
        _safe_file_size_check(path, max_file_size_mb)
        md = self._get_instance()
        try:
            if hasattr(md, "convert_local"):
                result = md.convert_local(str(path))
            else:
                result = md.convert(str(path))
        except DocumentParseError:
            raise
        except Exception as e:
            raise DocumentParseError(
                f"markitdown conversion failed: {type(e).__name__}: {e}",
                original_error=e,
                retryable=False,
            ) from e

        text_content = ""
        if isinstance(result, dict):
            text_content = str(result.get("text_content") or result.get("markdown") or result.get("text") or "")
        else:
            for attr in ("text_content", "markdown", "text"):
                val = getattr(result, attr, None)
                if val:
                    text_content = str(val)
                    break
        pages: list[str] = []
        if text_content:
            page_splits = [
                p for p in re.split(r"<!--\s*page\s+\d+\s*-->", text_content) if p.strip()
            ]
            if len(page_splits) >= 2:
                pages = [p.strip() for p in page_splits]
            else:
                chunks = text_content.split("\n\n\n")
                if len(chunks) >= 2:
                    pages = [c.strip() for c in chunks if c.strip()]
        tables = extract_markdown_tables(text_content)
        metadata = _file_meta(path)
        if isinstance(result, dict):
            for key in ("title", "author", "subject", "keywords", "creator", "producer", "pages"):
                if key in result and result[key] is not None:
                    metadata[key] = result[key]
        else:
            for key in ("title", "author", "subject", "keywords", "creator", "producer"):
                val = getattr(result, key, None)
                if val not in (None, ""):
                    metadata[key] = val
            pages_count = getattr(result, "page_count", None) or getattr(result, "pages", None)
            if pages_count:
                metadata["pages"] = pages_count
        plugin_label = "+ocr" if self.enable_plugins and self.llm_client is not None else ""
        return ParsedDocument(
            file_path=str(path),
            file_type=file_type,
            text=text_content,
            pages=pages,
            tables=tables,
            markdown=text_content,
            metadata=metadata,
            parser_used=f"markitdown{plugin_label}",
        )
