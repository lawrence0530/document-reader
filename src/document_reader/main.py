"""
Document Reader Skill CLI entrypoint. Invoked by run.bat / run.sh via `uv run`.
Agents SHOULD always go through run.{bat,sh}; this script parses args -> calls
DocumentReader -> prints JSON.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path
from typing import Any

if sys.platform.startswith("win"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from document_reader import DocumentReader, DocumentParseError, ParsedDocument

import httpx
from markitdown import MarkItDown  # noqa: F401  (dependency probe marker)
from openai import OpenAI


def _first_env(*keys: str) -> str | None:
    for k in keys:
        v = os.environ.get(k)
        if v:
            return v
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="document-reader",
        description=(
            "Document Reader Skill CLI. Parses PDF / Office / Image / Text files "
            "and prints a structured ParsedDocument to stdout."
        ),
    )
    p.add_argument(
        "files",
        nargs="+",
        help="One or more local files to parse (absolute or relative paths)",
    )
    p.add_argument(
        "--file-type-hint",
        dest="file_type_hint",
        default=None,
        help=(
            "Override extension detection, e.g. pdf, docx, xlsx, pptx, doc, xls, ppt, "
            "jpg, png, txt, csv, json, xml, md, html, epub, zip. Required when the "
            "file has no extension."
        ),
    )
    p.add_argument(
        "--password", default=None, help="Password for encrypted PDF / Office files"
    )
    p.add_argument(
        "--pages",
        default=None,
        help="PDF page range: '1-20' / '3' / '1,3,5-7'",
    )
    p.add_argument(
        "--ocr",
        action="store_true",
        default=False,
        help="Enable MinerU cloud OCR for scanned pages / embedded screenshots",
    )
    p.add_argument(
        "--mineru-mode",
        dest="mineru_mode",
        choices=["flash", "precision"],
        default=None,
        help="Force MinerU mode: flash (fast/small) / precision (accurate, requires MINERU_TOKEN)",
    )
    p.add_argument(
        "--mineru-token",
        dest="mineru_token",
        default=None,
        help="MinerU SDK token. If omitted, falls back to MINERU_TOKEN env var.",
    )
    p.add_argument(
        "--llm-api-key",
        dest="llm_api_key",
        default=None,
        help=(
            "LLM API key for offline markitdown-ocr plugin. If omitted, reads from "
            "LLM_API_KEY, OPENAI_API_KEY, or AZURE_OPENAI_API_KEY env vars."
        ),
    )
    p.add_argument(
        "--llm-base-url",
        dest="llm_base_url",
        default=None,
        help="Optional LLM base URL for offline OCR plugin (e.g. proxy endpoint).",
    )
    p.add_argument(
        "--llm-model",
        dest="llm_model",
        default=None,
        help="Optional LLM model name for offline OCR plugin.",
    )
    p.add_argument(
        "--enable-ocr-plugin",
        dest="enable_ocr_plugin",
        action="store_true",
        default=False,
        help=(
            "Enable the offline markitdown-ocr plugin for scanned PDFs / embedded "
            "Office screenshots. An LLM API key is required; --ocr (MinerU cloud) "
            "is the recommended default when online."
        ),
    )
    p.add_argument(
        "--max-file-size-mb",
        dest="max_file_size_mb",
        type=int,
        default=500,
        help="Maximum allowed file size in MB per file (default 500).",
    )
    p.add_argument(
        "--output-format",
        dest="output_format",
        choices=["json", "text", "markdown", "preview"],
        default="json",
        help=(
            "Output format: json (default, full structured) / text (truncate .text) "
            "/ markdown (truncate .markdown) / preview (summary + first N chars)."
        ),
    )
    p.add_argument(
        "--chars",
        type=int,
        default=2000,
        help="Max characters to keep for text / markdown / preview (default 2000).",
    )
    p.add_argument(
        "-o",
        "--output-dir",
        dest="output_dir",
        default="./output",
        help=(
            "Directory for parsed output files. Each source file gets a "
            "subfolder named after its basename. Default: ./output"
        ),
    )
    return p


def truncate(text: str, chars: int) -> str:
    if text is None:
        return ""
    if len(text) <= chars:
        return text
    return text[:chars] + "...<truncated>"


def render_result(doc: ParsedDocument, fmt: str, chars: int) -> str:
    if fmt == "text":
        return truncate(doc.text or "", chars)
    if fmt == "markdown":
        return truncate(doc.markdown or doc.text or "", chars)
    if fmt == "preview":
        return (
            f"file_path: {doc.file_path}\n"
            f"file_type: {doc.file_type}\n"
            f"method: {doc.parser_used}\n"
            f"chars: {len(doc.text or '')}\n"
            f"pages: {len(doc.pages)}\n"
            f"tables: {len(doc.tables)}\n"
            "--- preview ---\n"
            f"{doc.preview_chars(chars)}"
        )
    return json.dumps(doc.to_dict(), ensure_ascii=False, indent=2)


def render_error(e: DocumentParseError, file_arg: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "file": file_arg,
            "error": str(e),
            "retryable": bool(getattr(e, "retryable", False)),
            "original_error_type": (
                type(e.original_error).__name__
                if getattr(e, "original_error", None) is not None
                else type(e).__name__
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


def extra_kwargs_from_args(args: argparse.Namespace) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    if args.mineru_mode in ("flash", "precision"):
        kw["mineru_mode"] = args.mineru_mode
    if args.ocr:
        kw["is_ocr"] = True
    return kw


def _build_offline_llm_client(args: argparse.Namespace) -> Any | None:
    if not args.enable_ocr_plugin:
        return None
    api_key = args.llm_api_key or _first_env(
        "LLM_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"
    )
    if not api_key:
        return None
    kwargs: dict[str, Any] = {"api_key": api_key}
    if args.llm_base_url:
        kwargs["base_url"] = args.llm_base_url
    kwargs.setdefault("http_client", httpx.Client(timeout=120))
    return OpenAI(**kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    files: list[Path] = [Path(f).expanduser().resolve() for f in args.files]
    early_fail = False
    for f in files:
        if not f.exists():
            payload = {
                "ok": False,
                "file": str(f),
                "error": f"File not found: {f}",
                "retryable": False,
                "original_error_type": "FileNotFoundError",
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            early_fail = True
    if early_fail:
        return 2

    try:
        llm_client = _build_offline_llm_client(args)
        mineru_token = args.mineru_token or _first_env("MINERU_TOKEN")
        reader = DocumentReader(
            mineru_token=mineru_token,
            enable_markitdown_ocr=args.enable_ocr_plugin,
            llm_client=llm_client,
            llm_model=args.llm_model,
            max_file_size_mb=args.max_file_size_mb,
        )
        extra = extra_kwargs_from_args(args)
        results = reader.read_batch(
            files,
            args.output_dir,
            fail_fast=False,
            file_type_hint=args.file_type_hint,
            password=args.password,
            pages=args.pages,
            **extra,
        )
    except DocumentParseError as e:
        print(render_error(e, str(files[0]) if files else ""))
        return 3
    except Exception as e:  # pragma: no cover
        wrapped = DocumentParseError(
            f"Unexpected error: {e}", original_error=e, retryable=False
        )
        print(render_error(wrapped, str(files[0]) if files else ""))
        return 4

    output_root = Path(args.output_dir).expanduser().resolve()
    any_error = False
    for i, (f, res) in enumerate(zip(files, results)):
        saved_dir = output_root / f.stem
        if isinstance(res, DocumentParseError):
            any_error = True
            print(render_error(res, str(f)))
            continue
        try:
            saved_dir.mkdir(parents=True, exist_ok=True)
            (saved_dir / "result.json").write_text(
                json.dumps(res.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"Saved to: {saved_dir}")
        except Exception as e:  # pragma: no cover
            any_error = True
            wrapped = DocumentParseError(
                f"Failed to write result for {f.name}: {type(e).__name__}: {e}",
                original_error=e,
                retryable=False,
            )
            print(render_error(wrapped, str(f)))
    return 3 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
