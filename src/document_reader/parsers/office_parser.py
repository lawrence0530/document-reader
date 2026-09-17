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

    def parse_batch(
        self,
        items: list[tuple[str | Path, str | Path]],
        *,
        fail_fast: bool = False,
        file_type: str = "docx",
        max_file_size_mb: int = 500,
        **extra: Any,
    ) -> list[ParsedDocument | DocumentParseError]:
        sdk_opts: dict[str, Any] = {}
        mineru_mode = extra.get("mineru_mode")
        if mineru_mode is not None:
            sdk_opts["mineru_mode"] = mineru_mode
        mineru_results: list[ParsedDocument | DocumentParseError | None] = [None] * len(items)
        if self.mineru is not None:
            try:
                raw = self.mineru.parse_batch(
                    items,
                    file_type=file_type,
                    max_file_size_mb=max_file_size_mb,
                    fail_fast=fail_fast,
                    **sdk_opts,
                )
                for i, r in enumerate(raw):
                    if i < len(mineru_results):
                        mineru_results[i] = r
            except DocumentParseError as e:
                if fail_fast:
                    raise
                for i in range(len(items)):
                    mineru_results[i] = e
            except Exception as e:
                wrapped = DocumentParseError(
                    f"MinerU batch init error (office): {type(e).__name__}: {e}",
                    original_error=e,
                    retryable=False,
                )
                if fail_fast:
                    raise wrapped from e
                for i in range(len(items)):
                    mineru_results[i] = wrapped
        results: list[ParsedDocument | DocumentParseError] = []
        group = _OFFICE_GROUP.get(file_type, "word")
        for i, (file_path, output_dir) in enumerate(items):
            path = Path(file_path).resolve()
            out_dir = Path(output_dir).expanduser().resolve()
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    raise DocumentParseError(f"File not found: {path}")
                _safe_file_size_check(path, max_file_size_mb)
            except DocumentParseError as e:
                if fail_fast:
                    raise
                results.append(e)
                continue
            except Exception as e:
                wrapped = DocumentParseError(
                    f"Office prepare error[{type(e).__name__}]: {e}",
                    original_error=e,
                    retryable=False,
                )
                if fail_fast:
                    raise wrapped from e
                results.append(wrapped)
                continue
            doc_or_err = mineru_results[i] if i < len(mineru_results) else None
            chain_used: list[str] = []
            if isinstance(doc_or_err, ParsedDocument):
                if group == "excel":
                    try:
                        pandas_md = self._pandas_excel_to_markdown(path)
                        md_path = out_dir / "full.md"
                        current_md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
                        if len(pandas_md) > len(current_md):
                            md_path.write_text(pandas_md, encoding="utf-8")
                    except Exception as ee:
                        log.debug("pandas excel overwrite skipped for %s: %s", path.name, ee)
                results.append(doc_or_err)
                continue
            if isinstance(doc_or_err, DocumentParseError):
                cause = type(doc_or_err.original_error or doc_or_err).__name__
                chain_used.append(f"mineru-open-sdk(failed {cause})")
                log.warning("Office L1 mineru-sdk (%s) batch failed for %s: %s", file_type, path.name, doc_or_err)
            md = self.markitdown or MarkItDownWrapper(enable_plugins=False)
            try:
                doc = md.parse(path, out_dir, file_type=file_type, max_file_size_mb=max_file_size_mb)
                if group == "excel":
                    pandas_md = self._pandas_excel_to_markdown(path)
                    current_md = (out_dir / "full.md").read_text(encoding="utf-8") or ""
                    if len(pandas_md) > len(current_md):
                        (out_dir / "full.md").write_text(pandas_md, encoding="utf-8")
                if chain_used:
                    doc.parser_used = " -> ".join([*chain_used, doc.parser_used])
                results.append(doc)
                continue
            except DocumentParseError as e:
                chain_used.append("markitdown(failed " + type(e.original_error or e).__name__ + ")")
            chain = " -> ".join(chain_used) if chain_used else "no parsers tried"
            extra_hint = ""
            if file_type in ("doc", "xls", "ppt"):
                extra_hint = (
                    f"\nHint: legacy .{file_type} binary format has limited local support. "
                    f"Convert first: `soffice --convert-to {file_type}x {path.name}` (LibreOffice required)."
                )
            results.append(
                DocumentParseError(
                    f"Unable to parse Office file ({file_type}). Chain: {chain}.{extra_hint}",
                    retryable=False,
                )
            )
        return results

    @staticmethod
    def _pandas_excel_to_markdown(path: Path) -> str:
        parts: list[str] = []
        try:
            import pandas as pd

            sheets = pd.read_excel(path, sheet_name=None, nrows=1000)
            for sheet_name, df in sheets.items():
                preview = df.head(100)
                parts.append(f"## Sheet: {sheet_name}")
                parts.append(preview.to_markdown(index=False))
        except Exception as e:
            log.debug("pandas excel preview failed for %s: %s", path, e)
        return "\n\n".join(parts)

    def parse(
        self,
        file_path: str | Path,
        output_dir: str | Path,
        file_type: str = "docx",
        max_file_size_mb: int = 500,
        **extra: Any,
    ) -> ParsedDocument:
        results = self.parse_batch(
            [(file_path, output_dir)],
            file_type=file_type,
            max_file_size_mb=max_file_size_mb,
            fail_fast=True,
            **extra,
        )
        if not results:
            raise DocumentParseError("OfficeParser.parse_batch returned empty for single-file input")
        doc = results[0]
        if isinstance(doc, DocumentParseError):
            raise doc
        return doc
