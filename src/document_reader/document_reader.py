from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from .models.document import ParsedDocument
from .parsers.base_parser import DocumentParseError
from .parsers.image_parser import ImageParser
from .parsers.markitdown_wrapper import MarkItDownWrapper
from .parsers.mineru_sdk_parser import MinerUSdkParser
from .parsers.office_parser import OfficeParser
from .parsers.pdf_parser import PdfParser
from .parsers.text_parser import TextParser
from .utils.dependency_checker import (
    is_markitdown_available,
    is_markitdown_ocr_plugin_available,
    is_mineru_sdk_available,
    is_network_available,
    python_version_ok,
)
from .utils.file_type_detector import (
    IMAGE_TYPES,
    OFFICE_TYPES,
    TEXT_LIKE_TYPES,
    detect_file_type,
)

log = logging.getLogger(__name__)


class DocumentReader:
    def __init__(
        self,
        # --- mineru-open-sdk ---
        mineru_token: str | None = None,
        mineru_ocr: bool | None = None,
        mineru_language: str = "ch",
        mineru_model: str | None = None,
        mineru_formula: bool = True,
        mineru_table: bool = True,
        mineru_timeout: float | None = None,
        mineru_base_url: str | None = None,
        # --- markitdown-ocr plugin ---
        enable_markitdown_ocr: bool = False,
        llm_client: Any | None = None,
        llm_model: str | None = None,
        llm_prompt: str | None = None,
        # --- general ---
        max_file_size_mb: int = 500,
        python_version_strict: bool = True,
    ) -> None:
        if python_version_strict and not python_version_ok(3, 12):
            raise RuntimeError(
                f"DocumentReader requires Python 3.12+. Current: {sys.version.split()[0]} "
                "(install uv and open this repo, uv will auto-download Python 3.12 from .python-version)"
            )
        self.max_file_size_mb = int(max_file_size_mb)
        self.enable_markitdown_ocr = bool(enable_markitdown_ocr)

        self._mineru: MinerUSdkParser | None = None
        if is_mineru_sdk_available():
            try:
                self._mineru = MinerUSdkParser(
                    token=mineru_token,
                    ocr=mineru_ocr,
                    language=mineru_language,
                    model=mineru_model,
                    formula=mineru_formula,
                    table=mineru_table,
                    timeout=mineru_timeout,
                    base_url=mineru_base_url,
                )
            except Exception as e:
                log.warning("MinerU parser init failed, will skip. Error: %s", e)
                self._mineru = None

        self._markitdown: MarkItDownWrapper | None = None
        self._markitdown_ocr: MarkItDownWrapper | None = None
        if is_markitdown_available():
            try:
                self._markitdown = MarkItDownWrapper(
                    enable_plugins=False,
                    llm_client=llm_client,
                    llm_model=llm_model,
                    llm_prompt=llm_prompt,
                )
                if (
                    self.enable_markitdown_ocr
                    and llm_client is not None
                    and is_markitdown_ocr_plugin_available()
                ):
                    self._markitdown_ocr = MarkItDownWrapper(
                        enable_plugins=True,
                        llm_client=llm_client,
                        llm_model=llm_model,
                        llm_prompt=llm_prompt,
                    )
            except Exception as e:
                log.warning("MarkItDown init failed: %s", e)

        self._llm_client = llm_client
        self._llm_model = llm_model
        self._text = TextParser()
        self._image = ImageParser(
            mineru_parser=self._mineru,
            markitdown_wrapper=self._markitdown,
            llm_client=llm_client,
            llm_model=llm_model,
        )
        self._office = OfficeParser(
            mineru_parser=self._mineru,
            markitdown_wrapper=self._markitdown,
        )
        self._pdf = PdfParser(
            mineru_parser=self._mineru,
            markitdown_wrapper=self._markitdown,
            markitdown_ocr_wrapper=self._markitdown_ocr,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def read(
        self,
        file_path: str | Path,
        output_dir: str | Path,
        *,
        file_type_hint: str | None = None,
        password: str | None = None,
        pages: str | None = None,
        is_ocr: bool | None = None,
        **extra: Any,
    ) -> ParsedDocument:
        results = self.read_batch(
            [file_path],
            output_dir,
            fail_fast=True,
            file_type_hint=file_type_hint,
            password=password,
            pages=pages,
            is_ocr=is_ocr,
            **extra,
        )
        if not results:
            raise DocumentParseError("read_batch returned empty for single-file input")
        doc = results[0]
        if isinstance(doc, DocumentParseError):
            raise doc
        return doc

    def read_batch(
        self,
        file_paths: list[str | Path],
        output_dir: str | Path,
        *,
        fail_fast: bool = False,
        file_type_hint: str | None = None,
        password: str | None = None,
        pages: str | None = None,
        is_ocr: bool | None = None,
        **extra: Any,
    ) -> list[ParsedDocument | DocumentParseError]:
        results: list[ParsedDocument | DocumentParseError | None] = [None] * len(file_paths)
        prepared: list[tuple[int, Path, Path, str, dict[str, Any]] | None] = [None] * len(file_paths)
        for i, raw_path in enumerate(file_paths):
            try:
                path = Path(raw_path).resolve()
                if not path.exists():
                    raise DocumentParseError(f"File not found: {path}")
                if not path.is_file():
                    raise DocumentParseError(f"Not a regular file: {path}")
                try:
                    ftype = detect_file_type(path, override=file_type_hint)
                except Exception as e:
                    raise DocumentParseError(
                        f"Failed to detect file type for {path.name}: {e}. "
                        f"Pass file_type_hint to override (e.g. file_type_hint='pdf').",
                        original_error=e,
                        retryable=False,
                    ) from e
                target_out = Path(output_dir).expanduser().resolve() / path.stem
                common: dict[str, Any] = dict(
                    output_dir=str(target_out),
                    file_type=ftype,
                    max_file_size_mb=self.max_file_size_mb,
                    password=password,
                    pages=pages,
                    is_ocr=is_ocr,
                    **extra,
                )
                prepared[i] = (i, path, target_out, ftype, common)
            except DocumentParseError as e:
                if fail_fast:
                    raise
                results[i] = e
            except Exception as e:
                wrapped = DocumentParseError(
                    f"Unexpected error preparing {raw_path}: {type(e).__name__}: {e}",
                    original_error=e,
                    retryable=False,
                )
                if fail_fast:
                    raise wrapped from e
                results[i] = wrapped
        pdf_group: list[tuple[int, Path, Path, str, dict[str, Any]]] = []
        office_group: list[tuple[int, Path, Path, str, dict[str, Any]]] = []
        image_group: list[tuple[int, Path, Path, str, dict[str, Any]]] = []
        text_group: list[tuple[int, Path, Path, str, dict[str, Any]]] = []
        unknown_group: list[tuple[int, Path, Path, str, dict[str, Any]]] = []
        for item in prepared:
            if item is None:
                continue
            _, _, _, ftype, _ = item
            if ftype == "pdf":
                pdf_group.append(item)
            elif ftype in OFFICE_TYPES:
                office_group.append(item)
            elif ftype in IMAGE_TYPES:
                image_group.append(item)
            elif ftype in TEXT_LIKE_TYPES:
                text_group.append(item)
            else:
                unknown_group.append(item)
        for group, parser_fn, parser_has_batch in (
            (pdf_group, self._pdf, True),
            (office_group, self._office, True),
            (image_group, self._image, False),
            (text_group, self._text, False),
            (unknown_group, None, False),
        ):
            if not group:
                continue
            if parser_has_batch and parser_fn is not None and hasattr(parser_fn, "parse_batch"):
                ftype_shared = group[0][3]
                all_same = all(it[3] == ftype_shared for it in group)
                if all_same:
                    items: list[tuple[Path, Path]] = [(it[1], it[2]) for it in group]
                    shared_kw = dict(group[0][4])
                    shared_kw.pop("output_dir", None)
                    shared_kw.pop("file_type", None)
                    try:
                        batch_results = parser_fn.parse_batch(
                            items,
                            fail_fast=fail_fast,
                            file_type=ftype_shared,
                            **shared_kw,
                        )
                        for j, (orig_idx, _, _, _, _) in enumerate(group):
                            if j < len(batch_results):
                                results[orig_idx] = batch_results[j]
                            else:
                                results[orig_idx] = DocumentParseError(
                                    f"Batch parser returned fewer results than inputs for {ftype_shared}",
                                    retryable=False,
                                )
                        continue
                    except DocumentParseError:
                        if fail_fast:
                            raise
                    except Exception as e:
                        wrapped = DocumentParseError(
                            f"Batch parser setup error ({ftype_shared}): {type(e).__name__}: {e}",
                            original_error=e,
                            retryable=False,
                        )
                        if fail_fast:
                            raise wrapped from e
                        for orig_idx, _, _, _, _ in group:
                            results[orig_idx] = wrapped
                        continue
            for orig_idx, path, target_out, ftype, common in group:
                try:
                    if ftype == "pdf":
                        doc = self._pdf.parse(path, **common)
                    elif ftype in OFFICE_TYPES:
                        doc = self._office.parse(path, **common)
                    elif ftype in IMAGE_TYPES:
                        doc = self._image.parse(path, **common)
                    elif ftype in TEXT_LIKE_TYPES:
                        doc = self._text.parse(path, **common)
                    elif ftype == "unknown":
                        try:
                            doc = self._text.parse(path, **common)
                        except Exception as te:
                            log.debug("Text parser failed for unknown type (%s): %s", path.name, te)
                            if self._markitdown is not None:
                                doc = self._markitdown.parse(path, **common)
                            else:
                                raise
                    else:
                        if self._markitdown is not None:
                            doc = self._markitdown.parse(path, **common)
                        else:
                            doc = self._text.parse(path, **common)
                    results[orig_idx] = doc
                except DocumentParseError as e:
                    if fail_fast:
                        raise
                    results[orig_idx] = e
                except Exception as e:
                    wrapped = DocumentParseError(
                        f"Unexpected error reading {path}: {type(e).__name__}: {e}",
                        original_error=e,
                        retryable=False,
                    )
                    if fail_fast:
                        raise wrapped from e
                    results[orig_idx] = wrapped
        final: list[ParsedDocument | DocumentParseError] = []
        for i, r in enumerate(results):
            if r is None:
                final.append(
                    DocumentParseError(
                        f"No parser produced a result for index {i} ({file_paths[i]})",
                        retryable=False,
                    )
                )
            else:
                final.append(r)
        return final

    # ------------------------------------------------------------------
    # Info helpers
    # ------------------------------------------------------------------
    def capability(self) -> dict[str, Any]:
        return {
            "python_ok": python_version_ok(),
            "mineru_sdk": self._mineru is not None,
            "mineru_has_token": bool(getattr(self._mineru, "token", None) is not None),
            "markitdown": self._markitdown is not None,
            "markitdown_ocr_enabled": self._markitdown_ocr is not None,
            "markitdown_ocr_plugin_installed": is_markitdown_ocr_plugin_available(),
            "network": is_network_available(),
            "max_file_size_mb": self.max_file_size_mb,
        }
