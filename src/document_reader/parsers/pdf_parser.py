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

_MIN_TEXT_LEN = 50


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

    @staticmethod
    def _pypdf_parse(
        path: Path, password: str | None = None
    ) -> tuple[str, list[str], dict[str, Any]]:
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
        return text, pages, metadata

    @staticmethod
    def _ocr_hint(doc: ParsedDocument) -> str:
        return (
            "PDF produced very little text — likely a scanned document. Available fixes:\n"
            "  1) (preferred) Use mineru-sdk with MINERU_TOKEN (from https://mineru.net/apiManage/token) + ocr=True\n"
            "  2) (offline) Install markitdown-ocr plugin (`pip install markitdown-ocr`) and construct DocumentReader\n"
            "     with enable_markitdown_ocr=True + llm_client=... + llm_model=... so each embedded page image is OCR'd via LLM Vision."
        )

    def parse(
        self,
        file_path: str | Path,
        file_type: str = "pdf",
        max_file_size_mb: int = 500,
        password: str | None = None,
        pages: str | None = None,
        **extra: Any,
    ) -> ParsedDocument:
        path = Path(file_path).resolve()
        if not path.exists():
            raise DocumentParseError(f"File not found: {path}")
        _safe_file_size_check(path, max_file_size_mb)
        chain: list[str] = []
        last_doc: ParsedDocument | None = None

        # L1: mineru-open-sdk (always first when available)
        if self.mineru is not None:
            try:
                sdk_opts: dict[str, Any] = {}
                if pages:
                    sdk_opts["pages"] = pages
                mineru_mode = extra.get("mineru_mode")
                if mineru_mode is not None:
                    sdk_opts["mineru_mode"] = mineru_mode
                doc = self.mineru.parse(
                    path,
                    file_type=file_type,
                    max_file_size_mb=max_file_size_mb,
                    **sdk_opts,
                )
                if len(doc.text.strip()) >= _MIN_TEXT_LEN:
                    return doc
                last_doc = doc
                log.warning("PDF mineru-sdk returned short text (%d chars), will try OCR/other branches", len(doc.text.strip()))
                chain.append(doc.parser_used)
            except DocumentParseError as e:
                log.warning("PDF L1 mineru-sdk failed: %s", e)
                chain.append("mineru-open-sdk(failed " + type(e.original_error or e).__name__ + ")")
                if not e.retryable:
                    pass

        # L2: markitdown[all] (standard plugins=False)
        md = self.markitdown or MarkItDownWrapper(enable_plugins=False)
        try:
            doc = md.parse(
                path,
                file_type=file_type,
                max_file_size_mb=max_file_size_mb,
            )
            if len(doc.text.strip()) >= _MIN_TEXT_LEN:
                if chain:
                    doc.parser_used = " -> ".join([*chain, doc.parser_used])
                return doc
            last_doc = doc
            chain.append(doc.parser_used)
        except DocumentParseError as e:
            log.warning("PDF L2 markitdown failed: %s", e)
            chain.append("markitdown(failed " + type(e.original_error or e).__name__ + ")")

        # L2.5: markitdown with plugins enabled (markitdown-ocr) IF available
        md_ocr = self.markitdown_ocr
        if md_ocr is None:
            # if caller wants plugins enabled, they provide pre-built wrapper; otherwise detect availability
            try:
                from ..utils.dependency_checker import is_markitdown_ocr_plugin_available
                if is_markitdown_ocr_plugin_available():
                    # Build a one-off with plugins=True IF we have a wrapper spec we can derive llm from md
                    md_ocr = MarkItDownWrapper(enable_plugins=True)
            except Exception as e:
                log.debug("skip markitdown-ocr auto-probe: %s", e)
        if md_ocr is not None:
            try:
                doc = md_ocr.parse(
                    path,
                    file_type=file_type,
                    max_file_size_mb=max_file_size_mb,
                )
                if len(doc.text.strip()) >= _MIN_TEXT_LEN:
                    doc.parser_used = " -> ".join([*chain, doc.parser_used]) if chain else doc.parser_used
                    return doc
                last_doc = doc
                chain.append(doc.parser_used)
            except DocumentParseError as e:
                log.warning("PDF L2.5 markitdown-ocr failed: %s", e)
                chain.append("markitdown-ocr(failed " + type(e.original_error or e).__name__ + ")")

        # L3: pypdf (lightest local)
        try:
            pypdf_text, pypdf_pages, pypdf_meta = self._pypdf_parse(path, password)
            meta = _file_meta(path)
            meta.update(pypdf_meta)
            doc = ParsedDocument(
                file_path=str(path),
                file_type=file_type,
                text=pypdf_text,
                pages=pypdf_pages,
                tables=[],
                markdown=pypdf_text,
                metadata=meta,
                parser_used="pypdf",
            )
            if len(pypdf_text.strip()) >= _MIN_TEXT_LEN:
                if chain:
                    doc.parser_used = " -> ".join([*chain, "pypdf"])
                return doc
            last_doc = doc
            chain.append("pypdf")
        except DocumentParseError:
            raise
        except Exception as e:
            chain.append("pypdf(failed " + type(e).__name__ + ")")
            log.warning("PDF L3 pypdf failed: %s", e)

        # All branches produced too little text -> surface last doc + helpful error hint + chain in parser_used
        if last_doc is not None:
            if chain:
                last_doc.parser_used = " -> ".join(chain)
            hint = self._ocr_hint(last_doc)
            if not last_doc.text.strip():
                last_doc.text = hint
            last_doc.metadata["scanned_pdf_hint"] = hint
            last_doc.metadata["ocr_attempted"] = chain
            return last_doc

        chain_str = " -> ".join(chain) if chain else "no parsers"
        raise DocumentParseError(
            f"Failed to parse PDF via all branches. Chain: {chain_str}. {self._ocr_hint(ParsedDocument('', ''))}",
            retryable=False,
        )
