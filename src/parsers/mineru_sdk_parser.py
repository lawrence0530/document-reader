from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..models.document import ParsedDocument
from .base_parser import (
    DocumentParseError,
    _file_meta,
    _safe_file_size_check,
)

log = logging.getLogger(__name__)

FLASH_MAX_MB = 10
FLASH_MAX_PAGES = 20


def _estimate_pages(path: Path, file_type: str) -> int | None:
    if file_type != "pdf":
        return None
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return len(reader.pages)
    except Exception as e:
        log.debug("pypdf page count failed for %s: %s", path, e)
        return None


def _pick_mode(size_mb: float, pages: int | None, has_token: bool) -> str:
    if (
        size_mb <= FLASH_MAX_MB
        and (pages is None or pages <= FLASH_MAX_PAGES)
    ):
        return "flash"
    if has_token:
        return "precision"
    return "flash_but_oversized"


class MinerUSdkParser:
    def __init__(
        self,
        token: str | None = None,
        *,
        ocr: bool | None = None,
        language: str = "ch",
        model: str | None = None,
        formula: bool = True,
        table: bool = True,
        timeout: float | None = None,
        base_url: str | None = None,
    ) -> None:
        self.token = token or os.environ.get("MINERU_TOKEN")
        self.ocr = ocr
        self.language = language
        self.model = model
        self.formula = formula
        self.table = table
        self.timeout = timeout
        self.base_url = base_url
        self._client: Any = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from mineru import MinerU
        except ImportError as e:
            raise DocumentParseError(
                "mineru-open-sdk not installed; pip install mineru-open-sdk",
                original_error=e,
                retryable=False,
            ) from e
        try:
            kwargs: dict[str, Any] = {}
            if self.token:
                kwargs["token"] = self.token
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = MinerU(**kwargs)
            return self._client
        except Exception as e:
            raise DocumentParseError(
                f"Failed to initialize MinerU client: {e}",
                original_error=e,
                retryable=False,
            ) from e

    @staticmethod
    def _extract_attrs(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return result
        out: dict[str, Any] = {}
        for key in ("markdown", "content_list", "images", "state", "progress", "text"):
            if hasattr(result, key):
                out[key] = getattr(result, key)
        return out

    @staticmethod
    def _parse_tables_from_content(content_list: Any) -> list[list[list[str]]]:
        tables: list[list[list[str]]] = []
        if not isinstance(content_list, list):
            return tables
        for item in content_list:
            if isinstance(item, dict):
                kind = item.get("type")
                body = item.get("content") or item.get("text")
            else:
                kind = getattr(item, "type", None)
                body = getattr(item, "content", None) or getattr(item, "text", None)
            if kind in ("table", "tables") and isinstance(body, list):
                tables.append([[str(c) for c in row] for row in body])
        return tables

    def _call_flash(self, client: Any, path: str, extra: dict[str, Any]) -> tuple[Any, str]:
        kwargs = dict(extra)
        kwargs.setdefault("file_path", path)
        if self.ocr is not None:
            kwargs.setdefault("is_ocr", bool(self.ocr))
        if self.timeout:
            kwargs.setdefault("timeout", self.timeout)
        try:
            return client.flash_extract(**kwargs), "mineru-open-sdk[flash]"
        except TypeError:
            kwargs.pop("is_ocr", None)
            return client.flash_extract(**kwargs), "mineru-open-sdk[flash]"

    def _call_precision(self, client: Any, path: str, extra: dict[str, Any]) -> tuple[Any, str]:
        kwargs = dict(extra)
        kwargs.setdefault("file_path", path)
        if self.ocr is not None:
            kwargs.setdefault("ocr", bool(self.ocr))
        kwargs.setdefault("formula", self.formula)
        kwargs.setdefault("table", self.table)
        kwargs.setdefault("language", self.language)
        if self.model:
            kwargs.setdefault("model", self.model)
        if self.timeout:
            kwargs.setdefault("timeout", self.timeout)
        return client.extract(**kwargs), "mineru-open-sdk[precision]"

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
        size_bytes = _safe_file_size_check(path, max_file_size_mb)
        size_mb = size_bytes / 1024 / 1024
        pages = _estimate_pages(path, file_type)
        has_token = bool(self.token)
        mode = _pick_mode(size_mb, pages, has_token)

        client = self._get_client()
        parser_label = ""
        try:
            if mode == "flash_but_oversized":
                log.warning(
                    "File %.1fMB (pages=%s) exceeds flash limits, no MINERU_TOKEN set; "
                    "will attempt flash anyway then fall back if server rejects",
                    size_mb, pages,
                )
                result, parser_label = self._call_flash(client, str(path), extra)
            elif mode == "precision":
                result, parser_label = self._call_precision(client, str(path), extra)
            else:
                result, parser_label = self._call_flash(client, str(path), extra)
        except DocumentParseError:
            raise
        except (TimeoutError, ConnectionError) as e:
            raise DocumentParseError(
                f"MinerU network error: {e}",
                original_error=e,
                retryable=True,
            ) from e
        except Exception as e:
            msg = str(e).lower()
            retryable = any(k in msg for k in ("network", "timeout", "503", "504", "502", "429", "connection"))
            raise DocumentParseError(
                f"MinerU SDK error[{type(e).__name__}]: {e}",
                original_error=e,
                retryable=retryable,
            ) from e

        r = self._extract_attrs(result)
        markdown = str(r.get("markdown") or r.get("text") or "")
        content_list = r.get("content_list")
        pages_text: list[str] = []
        if isinstance(content_list, list):
            current_page: list[str] = []
            for item in content_list:
                if isinstance(item, dict):
                    if item.get("type") == "page_break" or item.get("page"):
                        if current_page:
                            pages_text.append("\n".join(current_page))
                            current_page = []
                    txt = item.get("content") or item.get("text") or ""
                else:
                    txt = getattr(item, "content", None) or getattr(item, "text", None) or ""
                if txt:
                    current_page.append(str(txt))
            if current_page:
                pages_text.append("\n".join(current_page))

        tables = self._parse_tables_from_content(content_list)
        metadata = _file_meta(path)
        metadata.update(
            {
                "sdk_mode": mode,
                "sdk_parser": parser_label,
                "sdk_state": r.get("state"),
                "sdk_progress": r.get("progress"),
                "estimated_pages": pages,
            }
        )
        text = markdown
        if not text and pages_text:
            text = "\n".join(pages_text)
        return ParsedDocument(
            file_path=str(path),
            file_type=file_type,
            text=text,
            pages=pages_text,
            tables=tables,
            markdown=markdown,
            metadata=metadata,
            parser_used=parser_label,
        )
