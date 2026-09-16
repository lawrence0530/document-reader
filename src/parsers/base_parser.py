from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..models.document import ParsedDocument

log = logging.getLogger(__name__)


class DocumentParseError(Exception):
    def __init__(
        self,
        message: str,
        *,
        original_error: BaseException | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.original_error = original_error
        self.retryable = retryable


@runtime_checkable
class BaseParser(Protocol):
    def parse(self, file_path: str | Path, **options: Any) -> ParsedDocument: ...


def _safe_file_size_check(path: Path, max_mb: int) -> int:
    size_bytes = path.stat().st_size
    if max_mb and size_bytes > max_mb * 1024 * 1024:
        raise DocumentParseError(
            f"File too large: {size_bytes / 1024 / 1024:.1f}MB > max {max_mb}MB"
        )
    return size_bytes


def _file_meta(path: Path) -> dict[str, Any]:
    st = path.stat()
    return {
        "size_bytes": st.st_size,
        "size_mb": round(st.st_size / 1024 / 1024, 3),
        "mtime": st.st_mtime,
        "basename": os.path.basename(path),
    }
