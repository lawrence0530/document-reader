from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ..models.document import ParsedDocument
from .base_parser import (
    DocumentParseError,
    _file_meta,
    _safe_file_size_check,
)

log = logging.getLogger(__name__)

_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")


def _detect_encoding(raw: bytes) -> str | None:
    try:
        import chardet

        guess = chardet.detect(raw[: max(4096, min(len(raw), 65536))])
        if guess and guess.get("encoding") and guess.get("confidence", 0) > 0.5:
            return guess["encoding"]
    except Exception as e:
        log.debug("chardet failed: %s", e)
    return None


def _decode(raw: bytes) -> tuple[str, str]:
    guess = _detect_encoding(raw)
    order: list[str] = []
    if guess:
        order.append(guess)
    order.extend(list(_ENCODINGS))
    seen: set[str] = set()
    for enc in order:
        enc_l = enc.lower()
        if enc_l in seen:
            continue
        seen.add(enc_l)
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def _pretty_json(text: str) -> str:
    try:
        obj = json.loads(text)
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except Exception:
        return text


def _pretty_xml(text: str) -> str:
    try:
        root = ET.fromstring(text)
        ET.indent(root, space="  ")
        return ET.tostring(root, encoding="unicode", xml_declaration=True)
    except Exception:
        return text


def _read_csv(path: Path) -> tuple[str, list[list[list[str]]]]:
    import pandas as pd

    tables: list[list[list[str]]] = []
    try:
        df = pd.read_csv(path, nrows=1000)
    except Exception:
        df = pd.read_csv(path, encoding_errors="replace", on_bad_lines="skip", nrows=1000)
    preview_rows = df.head(100)
    header: list[str] = [str(c) for c in preview_rows.columns.tolist()]
    body: list[list[str]] = [
        [str(cell) if cell is not None else "" for cell in row]
        for row in preview_rows.values.tolist()
    ]
    if header or body:
        tables.append([header, *body])
    return preview_rows.to_string(index=False), tables


class TextParser:
    def parse(
        self,
        file_path: str | Path,
        file_type: str = "txt",
        max_file_size_mb: int = 500,
        password: str | None = None,
        **extra: Any,
    ) -> ParsedDocument:
        path = Path(file_path).resolve()
        if not path.exists():
            raise DocumentParseError(f"File not found: {path}")
        size_bytes = _safe_file_size_check(path, max_file_size_mb)
        raw = path.read_bytes()
        text, used_enc = _decode(raw)
        metadata = _file_meta(path)
        metadata["encoding_used"] = used_enc
        metadata["size_bytes"] = size_bytes

        tables: list[list[list[str]]] = []
        markdown = ""
        pages: list[str] = []

        try:
            if file_type == "csv":
                text, tables = _read_csv(path)
                if tables:
                    md_rows = ["| " + " | ".join(tables[0][0]) + " |",
                               "|" + "|".join(["---"] * len(tables[0][0])) + "|"]
                    for row in tables[0][1:]:
                        md_rows.append("| " + " | ".join(row) + " |")
                    markdown = "\n".join(md_rows)
            elif file_type == "json":
                text = _pretty_json(text)
                markdown = "```json\n" + text + "\n```"
            elif file_type == "xml":
                text = _pretty_xml(text)
                markdown = "```xml\n" + text + "\n```"
            elif file_type == "md":
                markdown = text
            elif file_type == "html":
                markdown = text
            elif file_type == "txt":
                markdown = text
            else:
                markdown = text
        except Exception as e:
            log.warning("Optional specialized formatting for %s failed: %s", file_type, e)

        if file_type in ("txt", "md", "html", "csv"):
            chunks = [c.strip() for c in text.split("\n\n\n") if c.strip()]
            if len(chunks) >= 2:
                pages = chunks

        return ParsedDocument(
            file_path=str(path),
            file_type=file_type,
            text=text,
            pages=pages,
            tables=tables,
            markdown=markdown or text,
            metadata=metadata,
            parser_used=f"text_parser[{file_type}]",
        )
