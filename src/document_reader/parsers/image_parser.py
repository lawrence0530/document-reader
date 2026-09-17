from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..models.document import ParsedDocument
from .base_parser import DocumentParseError, _file_meta, _safe_file_size_check
from .markitdown_wrapper import MarkItDownWrapper
from .mineru_sdk_parser import MinerUSdkParser

log = logging.getLogger(__name__)


class ImageParser:
    def __init__(
        self,
        mineru_parser: MinerUSdkParser | None = None,
        markitdown_wrapper: MarkItDownWrapper | None = None,
        llm_client: Any | None = None,
        llm_model: str | None = None,
    ) -> None:
        self.mineru = mineru_parser
        self.markitdown = markitdown_wrapper
        self.llm_client = llm_client
        self.llm_model = llm_model

    @staticmethod
    def _extract_image_meta(path: Path) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        try:
            from PIL import Image, ExifTags

            with Image.open(path) as im:
                meta["width"] = im.width
                meta["height"] = im.height
                meta["image_format"] = im.format
                meta["mode"] = im.mode
                exif_data = im.getexif()
                if exif_data:
                    tag_map = {ExifTags.TAGS.get(k, k): v for k, v in exif_data.items() if v}
                    if tag_map:
                        meta["exif"] = {k: str(v)[:200] for k, v in tag_map.items()}
        except Exception as e:
            log.debug("Pillow image meta failed for %s: %s", path, e)
        return meta

    def parse(
        self,
        file_path: str | Path,
        output_dir: str | Path,
        file_type: str = "image",
        max_file_size_mb: int = 500,
        is_ocr: bool | None = None,
        **extra: Any,
    ) -> ParsedDocument:
        path = Path(file_path).resolve()
        out_dir = Path(output_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            raise DocumentParseError(f"File not found: {path}")
        size_bytes = _safe_file_size_check(path, max_file_size_mb)

        chain_used: list[str] = []
        pillow_meta = self._extract_image_meta(path)

        if self.mineru is not None:
            try:
                sdk_opts: dict[str, Any] = {}
                if is_ocr is not None:
                    sdk_opts["is_ocr"] = bool(is_ocr)
                mineru_mode = extra.get("mineru_mode")
                if mineru_mode is not None:
                    sdk_opts["mineru_mode"] = mineru_mode
                doc = self.mineru.parse(
                    path,
                    out_dir,
                    file_type=file_type,
                    max_file_size_mb=max_file_size_mb,
                    **sdk_opts,
                )
                doc.metadata.update(pillow_meta)
                return doc
            except DocumentParseError as e:
                cause = type(e.original_error or e).__name__
                chain_used.append(f"mineru-open-sdk(failed {cause})")
                log.warning("Image L1 mineru-sdk failed: %s", e)

        md = self.markitdown or MarkItDownWrapper(
            enable_plugins=False,
            llm_client=self.llm_client,
            llm_model=self.llm_model,
        )
        try:
            doc = md.parse(
                path,
                out_dir,
                file_type=file_type,
                max_file_size_mb=max_file_size_mb,
            )
            doc.metadata.update(pillow_meta)
            if chain_used:
                doc.parser_used = " -> ".join([*chain_used, doc.parser_used])
            return doc
        except DocumentParseError as e:
            chain_used.append("markitdown(failed " + type(e.original_error or e).__name__ + ")")

        meta = _file_meta(path)
        meta.update(pillow_meta)
        meta["size_bytes"] = size_bytes
        fallback_md = (
            "![image](" + path.as_uri() + ")\n\n"
            + "Unable to extract descriptive text. To recognize text inside images:\n"
            + "  - Online: enable mineru-open-sdk and pass is_ocr=True\n"
            + "  - Offline: construct DocumentReader with llm_client + llm_model."
        )
        (out_dir / "full.md").write_text(fallback_md, encoding="utf-8")
        return ParsedDocument(
            file_path=str(path),
            file_type=file_type,
            text="",
            pages=[],
            tables=[],
            markdown="",
            metadata=meta,
            parser_used=" -> ".join(chain_used) if chain_used else "image_parser[meta-only]",
        )
