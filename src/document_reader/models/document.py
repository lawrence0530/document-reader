from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ParsedDocument:
    file_path: str
    file_type: str
    text: str = ""
    pages: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)
    markdown: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    parser_used: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2, ensure_ascii: bool = False) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=ensure_ascii)

    def preview_chars(self, n: int = 500) -> str:
        src = self.text or self.markdown or ""
        if len(src) <= n:
            return src
        return src[:n] + "...<truncated>"

    def __repr__(self) -> str:
        return (
            f"ParsedDocument(file_path={self.file_path!r}, "
            f"file_type={self.file_type!r}, "
            f"parser_used={self.parser_used!r}, "
            f"text_len={len(self.text)}, "
            f"pages={len(self.pages)}, "
            f"tables={len(self.tables)}, "
            f"preview={self.preview_chars(120)!r})"
        )
