from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..models.document import ParsedDocument
from .base_parser import (
    DocumentParseError,
    _file_meta,
    _safe_file_size_check,
)
from .markitdown_wrapper import MarkItDownWrapper
from .mineru_sdk_parser import MinerUSdkParser

log = logging.getLogger(__name__)


class PdfParser:
    def __init__(
        self,
        mineru_parser: MinerUSdkParser | None = None,
        markitdown_wrapper: MarkItDownWrapper | None = None,
        markitdown_ocr_wrapper: MarkItDownWrapper | None = None,
    ) -> None:
        self.mineru = mineru_parser
        self.markitdown = markitdown_wrapper
        self.markitdown_ocr = markitdown_ocr_wrapper

    def parse_batch(
        self,
        items: list[tuple[str | Path, str | Path]],
        *,
        fail_fast: bool = False,
        file_type: str = "pdf",
        max_file_size_mb: int = 500,
        password: str | None = None,
        pages: str | None = None,
        **extra: Any,
    ) -> list[ParsedDocument | DocumentParseError]:
        sdk_opts: dict[str, Any] = {}
        if pages:
            sdk_opts["pages"] = pages
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
                    f"MinerU batch init error: {type(e).__name__}: {e}",
                    original_error=e,
                    retryable=False,
                )
                if fail_fast:
                    raise wrapped from e
                for i in range(len(items)):
                    mineru_results[i] = wrapped
        results: list[ParsedDocument | DocumentParseError] = []
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
                    f"PDF prepare error[{type(e).__name__}]: {e}",
                    original_error=e,
                    retryable=False,
                )
                if fail_fast:
                    raise wrapped from e
                results.append(wrapped)
                continue
            doc_or_err = mineru_results[i] if i < len(mineru_results) else None
            chain: list[str] = []
            if isinstance(doc_or_err, ParsedDocument):
                results.append(doc_or_err)
                continue
            if isinstance(doc_or_err, DocumentParseError):
                cause = type(doc_or_err.original_error or doc_or_err).__name__
                chain.append(f"mineru-open-sdk(failed {cause})")
                log.warning("PDF L1 mineru-sdk batch failed for %s: %s", path.name, doc_or_err)
            md = self.markitdown or MarkItDownWrapper(enable_plugins=False)
            try:
                doc = md.parse(path, out_dir, file_type=file_type, max_file_size_mb=max_file_size_mb)
                if chain:
                    doc.parser_used = " -> ".join([*chain, doc.parser_used])
                results.append(doc)
                continue
            except DocumentParseError as e:
                chain.append("markitdown(failed " + type(e.original_error or e).__name__ + ")")
                log.warning("PDF L2 markitdown failed: %s", e)
            md_ocr = self.markitdown_ocr
            if md_ocr is None:
                try:
                    from ..utils.dependency_checker import is_markitdown_ocr_plugin_available
                    if is_markitdown_ocr_plugin_available():
                        md_ocr = MarkItDownWrapper(enable_plugins=True)
                except Exception as ee:
                    log.debug("skip markitdown-ocr auto-probe: %s", ee)
            if md_ocr is not None:
                try:
                    doc = md_ocr.parse(path, out_dir, file_type=file_type, max_file_size_mb=max_file_size_mb)
                    doc.parser_used = " -> ".join([*chain, doc.parser_used])
                    results.append(doc)
                    continue
                except DocumentParseError as e:
                    chain.append("markitdown-ocr(failed " + type(e.original_error or e).__name__ + ")")
                    log.warning("PDF L2.5 markitdown-ocr failed: %s", e)
            try:
                pypdf_meta = self._pypdf_write(path, out_dir, password)
                meta = _file_meta(path)
                meta.update(pypdf_meta)
                parser_used = "pypdf"
                if chain:
                    parser_used = " -> ".join([*chain, "pypdf"])
                results.append(
                    ParsedDocument(
                        file_path=str(path),
                        file_type=file_type,
                        text="",
                        pages=[],
                        tables=[],
                        markdown="",
                        metadata=meta,
                        parser_used=parser_used,
                    )
                )
                continue
            except DocumentParseError as e:
                if fail_fast:
                    raise
                chain.append("pypdf(failed " + type(e.original_error or e).__name__ + ")")
                log.warning("PDF L3 pypdf failed: %s", e)
                results.append(
                    DocumentParseError(
                        f"Failed to parse PDF. Chain: {' -> '.join(chain) if chain else 'no parsers'}.",
                        original_error=getattr(e, "original_error", None),
                        retryable=False,
                    )
                )
                continue
            except Exception as e:
                if fail_fast:
                    raise DocumentParseError(
                        f"Unexpected pypdf error[{type(e).__name__}]: {e}",
                        original_error=e,
                        retryable=False,
                    ) from e
                chain.append("pypdf(failed " + type(e).__name__ + ")")
                log.warning("PDF L3 pypdf failed: %s", e)
                results.append(
                    DocumentParseError(
                        f"Failed to parse PDF. Chain: {' -> '.join(chain) if chain else 'no parsers'}.",
                        original_error=e,
                        retryable=False,
                    )
                )
                continue
        return results

    @staticmethod
    def _pypdf_write(
        path: Path, out_dir: Path, password: str | None = None
    ) -> dict[str, Any]:
        from pypdf import PdfReader
        from pypdf.errors import EncryptedFileError

        reader = PdfReader(str(path))
        metadata: dict[str, Any] = {}
        if reader.is_encrypted:
            if password is None:
                raise DocumentParseError(
                    "PDF is encrypted. Please pass `password=...` to DocumentReader.read()",
                    retryable=False,
                )
            try:
                result = reader.decrypt(password)
                if not result:
                    raise DocumentParseError(
                        "PDF decrypt failed. Double-check password.",
                        retryable=False,
                    )
                metadata["pdf_decrypt"] = result
            except DocumentParseError:
                raise
            except EncryptedFileError as e:
                raise DocumentParseError(
                    f"PDF decrypt error: {e}",
                    original_error=e,
                    retryable=False,
                ) from e
        pages: list[str] = []
        for page in reader.pages:
            try:
                page_text = page.extract_text() or ""
            except Exception as e:
                log.debug("pypdf page extract failed: %s", e)
                page_text = ""
            pages.append(page_text)
        text = "\n\n".join(p for p in pages if p)
        meta_obj = reader.metadata
        if meta_obj:
            for attr in ("title", "author", "subject", "producer", "creator"):
                try:
                    val = getattr(meta_obj, attr, None)
                    if val:
                        metadata["pdf_" + attr] = str(val)
                except Exception:
                    continue
        try:
            metadata["pdf_pages"] = len(reader.pages)
        except Exception:
            pass
        (out_dir / "full.md").write_text(text, encoding="utf-8")
        return metadata

    def parse(
        self,
        file_path: str | Path,
        output_dir: str | Path,
        file_type: str = "pdf",
        max_file_size_mb: int = 500,
        password: str | None = None,
        pages: str | None = None,
        **extra: Any,
    ) -> ParsedDocument:
        results = self.parse_batch(
            [(file_path, output_dir)],
            file_type=file_type,
            max_file_size_mb=max_file_size_mb,
            password=password,
            pages=pages,
            fail_fast=True,
            **extra,
        )
        if not results:
            raise DocumentParseError("PdfParser.parse_batch returned empty for single-file input")
        doc = results[0]
        if isinstance(doc, DocumentParseError):
            raise doc
        return doc
