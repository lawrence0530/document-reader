from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

EXT_TO_TYPE: dict[tuple[str, ...], str] = {
    (".pdf",): "pdf",
    (".docx",): "docx",
    (".doc",): "doc",
    (".xlsx",): "xlsx",
    (".xls",): "xls",
    (".pptx",): "pptx",
    (".ppt",): "ppt",
    (".csv",): "csv",
    (".json",): "json",
    (".xml",): "xml",
    (".md", ".markdown"): "md",
    (".txt",): "txt",
    (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif"): "image",
    (".html", ".htm"): "html",
    (".epub",): "epub",
    (".zip",): "zip",
}

TEXT_LIKE_TYPES = {"txt", "csv", "json", "xml", "md", "html"}
OFFICE_TYPES = {"docx", "doc", "xlsx", "xls", "pptx", "ppt"}
IMAGE_TYPES = {"image"}


def _ext_label(path: Path) -> str:
    ext = path.suffix.lower()
    for exts, label in EXT_TO_TYPE.items():
        if ext in exts:
            return label
    return "unknown"


def _magic_label(path: Path) -> Optional[str]:
    try:
        import magic  # type: ignore
    except ImportError:
        return None
    try:
        mime = magic.from_file(str(path), mime=True)
    except Exception as e:
        log.debug("python-magic failed for %s: %s", path, e)
        return None

    if mime == "application/pdf":
        return "pdf"
    if mime.startswith("image/"):
        return "image"
    if mime in ("application/msword",):
        return "doc"
    if mime in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ):
        return "docx"
    if mime in ("application/vnd.ms-excel",):
        return "xls"
    if mime in ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",):
        return "xlsx"
    if mime in ("application/vnd.ms-powerpoint",):
        return "ppt"
    if mime in (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ):
        return "pptx"
    if mime.startswith("text/") or mime == "application/json":
        return "txt"
    return None


def detect_file_type(
    file_path: str | Path, *, use_magic: bool = True, override: Optional[str] = None
) -> str:
    if override:
        return override
    path = Path(file_path)
    ext_type = _ext_label(path)
    if ext_type != "unknown" or not use_magic:
        return ext_type
    magic_type = _magic_label(path)
    return magic_type if magic_type else "unknown"
