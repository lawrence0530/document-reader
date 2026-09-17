from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..models.document import ParsedDocument
from .base_parser import DocumentParseError, _safe_file_size_check
from .markitdown_wrapper import MarkItDownWrapper
from .mineru_sdk_parser import MinerUSdkParser

log = logging.getLogger(__name__)

_OFFICE_GROUP = {
    "doc": "word",
    "docx": "word",
    "xls": "excel",
    "xlsx": "excel",
    "ppt": "ppt",
    "pptx": "ppt",
}


class OfficeParser:
    def __init__(
        self,
        mineru_parser: MinerUSdkParser | None = None,
        markitdown_wrapper: MarkItDownWrapper | None = None,
    ) -> None:
        self.mineru = mineru_parser
        self.markitdown = markitdown_wrapper

    @staticmethod
    def _pandas_excel_preview(path: Path) -> tuple[str, list[list[list[str]]]]:
        tables: list[list[list[str]]] = []
        preview_parts: list[str] = []
        try:
            import pandas as pd

            sheets = pd.read_excel(path, sheet_name=None, nrows=1000)
            for sheet_name, df in sheets.items():
                preview = df.head(100)
                header = [str(c) for c in preview.columns.tolist()]
                body = [
                    [str(c) if c is not None else "" for c in row]
                    for row in preview.values.tolist()
                ]
                if header or body:
                    tables.append([header, *body])
                preview_parts.append(f"## Sheet: {sheet_name}\n" + preview.to_string(index=False))
        except Exception as e:
            log.debug("pandas excel preview failed for %s: %s", path, e)
        return "\n\n".join(preview_parts), tables

    def parse(
        self,
        file_path: str | Path,
        file_type: str = "docx",
        max_file_size_mb: int = 500,
        **extra: Any,
    ) -> ParsedDocument:
        path = Path(file_path).resolve()
        if not path.exists():
            raise DocumentParseError(f"File not found: {path}")
        _safe_file_size_check(path, max_file_size_mb)
        chain_used: list[str] = []
        group = _OFFICE_GROUP.get(file_type, "word")

        # L1: mineru-open-sdk (always first when available)
        if self.mineru is not None:
            try:
                sdk_opts: dict[str, Any] = {}
                mineru_mode = extra.get("mineru_mode")
                if mineru_mode is not None:
                    sdk_opts["mineru_mode"] = mineru_mode
                return self.mineru.parse(
                    path,
                    file_type=file_type,
                    max_file_size_mb=max_file_size_mb,
                    **sdk_opts,
                )
            except DocumentParseError as e:
                log.warning("Office L1 mineru-sdk (%s) failed: %s", file_type, e)
                chain_used.append("mineru-open-sdk(failed " + type(e.original_error or e).__name__ + ")")

        # L2: markitdown[all]
        md = self.markitdown or MarkItDownWrapper(enable_plugins=False)
        try:
            doc = md.parse(path, file_type=file_type, max_file_size_mb=max_file_size_mb)
            # For Excel, reinforce tables via pandas (more reliable than md table reverse parse)
            if group == "excel":
                pandas_text, pandas_tables = self._pandas_excel_preview(path)
                if pandas_tables:
                    if not doc.tables:
                        doc.tables = pandas_tables
                    if pandas_text and len(pandas_text) > len(doc.text):
                        doc.text = pandas_text
            if chain_used:
                doc.parser_used = " -> ".join([*chain_used, doc.parser_used])
            return doc
        except DocumentParseError as e:
            chain_used.append("markitdown(failed " + type(e.original_error or e).__name__ + ")")

        # Both failed -> helpful error
        chain = " -> ".join(chain_used) if chain_used else "no parsers tried"
        extra_hint = ""
        if file_type in ("doc", "xls", "ppt"):
            extra_hint = (
                f"\nHint: legacy .{file_type} binary format has limited local support. "
                f"Convert first: `soffice --convert-to {file_type}x {path.name}` (LibreOffice required)."
            )
        raise DocumentParseError(
            f"Unable to parse Office file ({file_type}). Chain: {chain}.{extra_hint}",
            retryable=False,
        )
