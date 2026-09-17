from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..models.document import ParsedDocument
from .base_parser import (
    DocumentParseError,
    _file_meta,
    _safe_file_size_check,
)

log = logging.getLogger(__name__)

FLASH_MAX_MB = 10
FLASH_MAX_PAGES = 20


def _estimate_pages(path: Path, file_type: str) -> int | None:
    if file_type != "pdf":
        return None
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        return len(reader.pages)
    except Exception as e:
        log.debug("pypdf page count failed for %s: %s", path, e)
        return None


def _pick_mode(
    size_mb: float,
    pages: int | None,
    has_token: bool,
    user_mode_override: str | None = None,
) -> str:
    if user_mode_override in ("flash", "precision"):
        return user_mode_override
    if has_token:
        return "precision"
    return "flash"


def _insert_sorted(results: list, idx: int, val: Any) -> None:
    while len(results) <= idx:
        results.append(None)
    results[idx] = val


class MinerUSdkParser:
    def __init__(
        self,
        token: str | None = None,
        *,
        ocr: bool | None = None,
        language: str = "ch",
        model: str | None = None,
        formula: bool = True,
        table: bool = True,
        timeout: float | None = None,
        base_url: str | None = None,
    ) -> None:
        self.token = token or os.environ.get("MINERU_TOKEN")
        self.ocr = ocr
        self.language = language
        self.model = model
        self.formula = formula
        self.table = table
        self.timeout = timeout
        self.base_url = base_url
        self._client: Any = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from mineru import MinerU
        except ImportError as e:
            raise DocumentParseError(
                "mineru-open-sdk not installed; pip install mineru-open-sdk",
                original_error=e,
                retryable=False,
            ) from e
        try:
            kwargs: dict[str, Any] = {}
            if self.token:
                kwargs["token"] = self.token
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = MinerU(**kwargs)
            return self._client
        except Exception as e:
            raise DocumentParseError(
                f"Failed to initialize MinerU client: {e}",
                original_error=e,
                retryable=False,
            ) from e

    @staticmethod
    def _extract_attrs(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return result
        out: dict[str, Any] = {}
        for key in (
            "markdown",
            "content_list",
            "images",
            "state",
            "progress",
            "text",
            "docx",
            "html",
            "latex",
            "task_id",
            "error",
            "err_code",
            "zip_url",
            "filename",
        ):
            if hasattr(result, key):
                out[key] = getattr(result, key)
        return out

    @staticmethod
    def _parse_tables_from_content(content_list: Any) -> list[list[list[str]]]:
        tables: list[list[list[str]]] = []
        if not isinstance(content_list, list):
            return tables
        for item in content_list:
            if isinstance(item, dict):
                kind = item.get("type")
                body = item.get("content") or item.get("text")
            else:
                kind = getattr(item, "type", None)
                body = getattr(item, "content", None) or getattr(item, "text", None)
            if kind in ("table", "tables") and isinstance(body, list):
                tables.append([[str(c) for c in row] for row in body])
        return tables

    def _call_flash(self, client: Any, path: str, extra: dict[str, Any]) -> tuple[Any, str]:
        kwargs = {k: v for k, v in extra.items() if k not in ("file_path", "file_type", "max_file_size_mb", "mineru_mode")}
        if self.ocr is not None:
            kwargs.setdefault("is_ocr", bool(self.ocr))
        if self.timeout:
            kwargs.setdefault("timeout", self.timeout)
        try:
            return client.flash_extract(path, **kwargs), "mineru-open-sdk[flash]"
        except TypeError:
            kwargs.pop("is_ocr", None)
            return client.flash_extract(path, **kwargs), "mineru-open-sdk[flash]"

    def _call_precision(self, client: Any, path: str, extra: dict[str, Any]) -> tuple[Any, str]:
        kwargs = {k: v for k, v in extra.items() if k not in ("file_path", "file_type", "max_file_size_mb", "mineru_mode")}
        if self.ocr is not None:
            kwargs.setdefault("ocr", bool(self.ocr))
        kwargs.setdefault("formula", self.formula)
        kwargs.setdefault("table", self.table)
        if self.language:
            kwargs.setdefault("language", self.language)
        if self.model:
            kwargs.setdefault("model", self.model)
        kwargs.setdefault("extra_formats", ["docx", "html", "latex"])
        if self.timeout:
            kwargs.setdefault("timeout", self.timeout)
        label = "mineru-open-sdk[precision]"
        return client.extract(path, **kwargs), label

    def _call_precision_batch(self, client: Any, paths: list[str], extra: dict[str, Any]) -> tuple[Any, str]:
        kwargs = {k: v for k, v in extra.items() if k not in ("file_path", "file_type", "max_file_size_mb", "mineru_mode")}
        if self.ocr is not None:
            kwargs.setdefault("ocr", bool(self.ocr))
        kwargs.setdefault("formula", self.formula)
        kwargs.setdefault("table", self.table)
        if self.language:
            kwargs.setdefault("language", self.language)
        if self.model:
            kwargs.setdefault("model", self.model)
        kwargs.setdefault("extra_formats", ["docx", "html", "latex"])
        if self.timeout:
            kwargs.setdefault("timeout", self.timeout)
        label = "mineru-open-sdk[precision]"
        if hasattr(client, "extract_batch") and callable(client.extract_batch):
            return client.extract_batch(paths, **kwargs), label
        raise DocumentParseError(
            "MinerU client missing extract_batch(); upgrade mineru-open-sdk",
            original_error=None,
            retryable=False,
        )

    def _materialize_doc(
        self,
        result: Any,
        path: Path,
        out_dir: Path,
        file_type: str,
        parser_label: str,
        pages: int | None,
        mode: str,
    ) -> ParsedDocument:
        r = self._extract_attrs(result)
        state = str(r.get("state") or "").lower()
        if state in ("failed", "error", "cancelled", "canceled", "timeout"):
            raise DocumentParseError(
                f"MinerU SDK returned failed state: {state or 'unknown'}"
                + (f" ({r.get('error')})" if r.get("error") else ""),
                original_error=None,
                retryable=True,
            )
        used_save_all = False
        if hasattr(result, "save_all") and callable(result.save_all):
            try:
                result.save_all(str(out_dir))
                used_save_all = True
            except Exception:
                used_save_all = False
        if not used_save_all:
            if hasattr(result, "save_markdown") and callable(result.save_markdown):
                try:
                    result.save_markdown(str(out_dir / "full.md"), with_images=True)
                except Exception:
                    md = str(getattr(result, "markdown", None) or r.get("markdown") or getattr(result, "text", None) or r.get("text") or "")
                    (out_dir / "full.md").write_text(md, encoding="utf-8")
            else:
                md = str(getattr(result, "markdown", None) or r.get("markdown") or getattr(result, "text", None) or r.get("text") or "")
                (out_dir / "full.md").write_text(md, encoding="utf-8")
            for name, method_suffix, ext in (
                ("docx", "save_docx", ".docx"),
                ("html", "save_html", ".html"),
                ("latex", "save_latex", ".latex"),
            ):
                method = getattr(result, method_suffix, None)
                if callable(method):
                    try:
                        method(str(out_dir / f"full{ext}"))
                        continue
                    except Exception:
                        pass
                content = r.get(name) or getattr(result, name, None)
                if isinstance(content, (bytes, bytearray)):
                    (out_dir / f"full{ext}").write_bytes(bytes(content))
                elif isinstance(content, str) and content.strip():
                    (out_dir / f"full{ext}").write_text(content, encoding="utf-8")
            images = r.get("images") or getattr(result, "images", None) or []
            if images:
                img_dir = out_dir / "images"
                img_dir.mkdir(parents=True, exist_ok=True)
                for i, obj in enumerate(images):
                    if obj is None:
                        continue
                    if hasattr(obj, "save") and callable(obj.save):
                        try:
                            default_name = getattr(obj, "name", None) or f"image_{i}.png"
                            p = img_dir / default_name
                            obj.save(str(p))
                            continue
                        except Exception:
                            pass
                    name = getattr(obj, "name", None) or f"image_{i}"
                    data = getattr(obj, "bytes", None)
                    raw: bytes | None = None
                    if isinstance(data, (bytes, bytearray)):
                        raw = bytes(data)
                    elif data is None:
                        if hasattr(obj, "save") and callable(obj.save):
                            import io as _bio

                            buf = _bio.BytesIO()
                            obj.save(buf, format=getattr(obj, "format", "PNG") or "PNG")
                            raw = buf.getvalue()
                    if not raw:
                        continue
                    fname = name if Path(name).suffix else (name + ".png")
                    (img_dir / fname).write_bytes(raw)
        metadata = _file_meta(path)
        metadata.update(
            {
                "sdk_mode": mode,
                "sdk_parser": parser_label,
                "sdk_state": r.get("state"),
                "sdk_progress": r.get("progress"),
                "estimated_pages": pages,
            }
        )
        if r.get("task_id"):
            metadata["sdk_task_id"] = r["task_id"]
        return ParsedDocument(
            file_path=str(path),
            file_type=file_type,
            text="",
            pages=[],
            tables=[],
            markdown="",
            metadata=metadata,
            parser_used=parser_label,
        )

    def parse_batch(
        self,
        items: list[tuple[str | Path, str | Path]],
        *,
        file_type: str = "pdf",
        max_file_size_mb: int = 500,
        mineru_mode: str | None = None,
        fail_fast: bool = False,
        **extra: Any,
    ) -> list[ParsedDocument | DocumentParseError]:
        results: list[ParsedDocument | DocumentParseError] = []
        prepared: list[tuple[Path, Path, int | None, str, str]] = []
        for file_path, output_dir in items:
            path = Path(file_path).resolve()
            out_dir = Path(output_dir).expanduser().resolve()
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    raise DocumentParseError(f"File not found: {path}")
                size_bytes = _safe_file_size_check(path, max_file_size_mb)
                size_mb = size_bytes / 1024 / 1024
                pages = _estimate_pages(path, file_type)
                has_token = bool(self.token)
                mode = _pick_mode(size_mb, pages, has_token, mineru_mode)
                parser_label = (
                    "mineru-open-sdk[precision]"
                    if mode == "precision"
                    else "mineru-open-sdk[flash]"
                )
                prepared.append((path, out_dir, pages, mode, parser_label))
            except DocumentParseError as e:
                if fail_fast:
                    raise
                results.append(e)
                prepared.append(None)  # type: ignore[arg-type]
            except Exception as e:
                wrapped = DocumentParseError(
                    f"Unexpected error preparing {path.name}: {type(e).__name__}: {e}",
                    original_error=e,
                    retryable=False,
                )
                if fail_fast:
                    raise wrapped from e
                results.append(wrapped)
                prepared.append(None)  # type: ignore[arg-type]
        indices = [i for i, p in enumerate(prepared) if p is not None]
        ok_items = [prepared[i] for i in indices]
        if not ok_items:
            return results
        paths_str = [str(p[0]) for p in ok_items]
        common_mode = ok_items[0][3]
        if any(p[3] != common_mode for p in ok_items):
            precision_group: list[tuple[int, tuple[Path, Path, int | None, str, str]]] = []
            flash_group: list[tuple[int, tuple[Path, Path, int | None, str, str]]] = []
            for idx, item in zip(indices, ok_items):
                (precision_group if item[3] == "precision" else flash_group).append((idx, item))
            for group_idx_group in ((precision_group, "precision"), (flash_group, "flash")):
                group_idx, group = group_idx_group
                if not group:
                    continue
                g_paths = [str(it[1][0]) for it in group]
                client = self._get_client()
                try:
                    if group_idx_group[1] == "precision":
                        it, label = self._call_precision_batch(client, g_paths, extra)
                        seen = 0
                        total = len(group)
                        try:
                            for sdk_res in it:
                                if seen >= total:
                                    break
                                g_idx, prep = group[seen]
                                _path, _out, _pages, _mode, _lab = prep
                                try:
                                    doc = self._materialize_doc(sdk_res, _path, _out, file_type, _lab, _pages, _mode)
                                    _insert_sorted(results, g_idx, doc)
                                except DocumentParseError as e:
                                    if fail_fast:
                                        raise
                                    _insert_sorted(results, g_idx, e)
                                except Exception as e:
                                    wrapped = DocumentParseError(
                                        f"MinerU SDK result error for {_path.name}: {type(e).__name__}: {e}",
                                        original_error=e,
                                        retryable=False,
                                    )
                                    if fail_fast:
                                        raise wrapped from e
                                    _insert_sorted(results, g_idx, wrapped)
                                seen += 1
                        except DocumentParseError:
                            raise
                        except (TimeoutError, ConnectionError) as e:
                            wrapped = DocumentParseError(f"MinerU network error: {e}", original_error=e, retryable=True)
                            if fail_fast:
                                raise wrapped from e
                            for j in range(seen, total):
                                g_idx, _ = group[j]
                                _insert_sorted(results, g_idx, wrapped)
                        except Exception as e:
                            _msg = str(e).lower()
                            _retry = any(k in _msg for k in ("network", "timeout", "503", "504", "502", "429", "connection", "unauthorized", "auth", "token"))
                            wrapped = DocumentParseError(
                                f"MinerU SDK error[{type(e).__name__}]: {e}",
                                original_error=e,
                                retryable=_retry,
                            )
                            if fail_fast:
                                raise wrapped from e
                            for j in range(seen, total):
                                g_idx, _ = group[j]
                                _insert_sorted(results, g_idx, wrapped)
                    else:
                        _sublist: list[ParsedDocument | DocumentParseError] = []
                        for g_idx, (_idx, prep) in group:
                            _path, _out, _pages, _mode, _lab = prep
                            try:
                                res, _ = self._call_flash(client, str(_path), extra)
                                doc = self._materialize_doc(res, _path, _out, file_type, _lab, _pages, _mode)
                                _sublist.append(doc)
                            except DocumentParseError as e:
                                if fail_fast:
                                    raise
                                _sublist.append(e)
                            except Exception as e:
                                _msg = str(e).lower()
                                _retry = any(k in _msg for k in ("network", "timeout", "503", "504", "502", "429", "connection", "unauthorized", "auth", "token"))
                                wrapped = DocumentParseError(
                                    f"MinerU SDK error[{type(e).__name__}]: {e}",
                                    original_error=e,
                                    retryable=_retry,
                                )
                                if fail_fast:
                                    raise wrapped from e
                                _sublist.append(wrapped)
                        for j, (g_idx, _) in enumerate(group):
                            _insert_sorted(results, g_idx, _sublist[j])
                except DocumentParseError:
                    raise
                except (TimeoutError, ConnectionError) as e:
                    wrapped = DocumentParseError(f"MinerU network error: {e}", original_error=e, retryable=True)
                    if fail_fast:
                        raise wrapped from e
                    for g_idx, _ in group:
                        _insert_sorted(results, g_idx, wrapped)
                except Exception as e:
                    _msg = str(e).lower()
                    _retry = any(k in _msg for k in ("network", "timeout", "503", "504", "502", "429", "connection", "unauthorized", "auth", "token"))
                    wrapped = DocumentParseError(
                        f"MinerU SDK batch error[{type(e).__name__}]: {e}",
                        original_error=e,
                        retryable=_retry,
                    )
                    if fail_fast:
                        raise wrapped from e
                    for g_idx, _ in group:
                        _insert_sorted(results, g_idx, wrapped)
            return results
        client = self._get_client()
        parser_label = ok_items[0][4]
        mode = common_mode
        if mode == "precision":
            try:
                it, label = self._call_precision_batch(client, paths_str, extra)
                parser_label = label
            except DocumentParseError:
                raise
            except (TimeoutError, ConnectionError) as e:
                wrapped = DocumentParseError(f"MinerU network error: {e}", original_error=e, retryable=True)
                if fail_fast:
                    raise wrapped from e
                for idx, _ in zip(indices, ok_items):
                    _insert_sorted(results, idx, wrapped)
                return results
            except Exception as e:
                msg = str(e).lower()
                retry = any(k in msg for k in ("network", "timeout", "503", "504", "502", "429", "connection", "unauthorized", "auth", "token"))
                wrapped = DocumentParseError(
                    f"MinerU SDK error[{type(e).__name__}]: {e}",
                    original_error=e,
                    retryable=retry,
                )
                if fail_fast:
                    raise wrapped from e
                for idx, _ in zip(indices, ok_items):
                    _insert_sorted(results, idx, wrapped)
                return results
            seen = 0
            total = len(ok_items)
            try:
                for sdk_res in it:
                    if seen >= total:
                        break
                    prep = ok_items[seen]
                    _path, _out, _pages, _mode, _lab = prep
                    try:
                        doc = self._materialize_doc(sdk_res, _path, _out, file_type, _lab or parser_label, _pages, _mode)
                        _insert_sorted(results, indices[seen], doc)
                    except DocumentParseError as e:
                        if fail_fast:
                            raise
                        _insert_sorted(results, indices[seen], e)
                    except Exception as e:
                        wrapped = DocumentParseError(
                            f"MinerU SDK result error for {_path.name}: {type(e).__name__}: {e}",
                            original_error=e,
                            retryable=False,
                        )
                        if fail_fast:
                            raise wrapped from e
                        _insert_sorted(results, indices[seen], wrapped)
                    seen += 1
            except DocumentParseError:
                raise
            except (TimeoutError, ConnectionError) as e:
                wrapped = DocumentParseError(f"MinerU network error: {e}", original_error=e, retryable=True)
                if fail_fast:
                    raise wrapped from e
                for j in range(seen, total):
                    _insert_sorted(results, indices[j], wrapped)
            except Exception as e:
                msg = str(e).lower()
                retry = any(k in msg for k in ("network", "timeout", "503", "504", "502", "429", "connection", "unauthorized", "auth", "token"))
                wrapped = DocumentParseError(
                    f"MinerU SDK error[{type(e).__name__}]: {e}",
                    original_error=e,
                    retryable=retry,
                )
                if fail_fast:
                    raise wrapped from e
                for j in range(seen, total):
                    _insert_sorted(results, indices[j], wrapped)
            return results
        for seen, (g_idx, prep) in enumerate(zip(indices, ok_items)):
            _path, _out, _pages, _mode, _lab = prep
            try:
                sdk_res, _ = self._call_flash(client, str(_path), extra)
                doc = self._materialize_doc(sdk_res, _path, _out, file_type, parser_label, _pages, _mode)
                _insert_sorted(results, g_idx, doc)
            except DocumentParseError as e:
                if fail_fast:
                    raise
                _insert_sorted(results, g_idx, e)
            except Exception as e:
                wrapped = DocumentParseError(
                    f"MinerU SDK error[{type(e).__name__}]: {e}",
                    original_error=e,
                    retryable=False,
                )
                if fail_fast:
                    raise wrapped from e
                _insert_sorted(results, g_idx, wrapped)
        return results

    def parse(
        self,
        file_path: str | Path,
        output_dir: str | Path,
        file_type: str = "pdf",
        max_file_size_mb: int = 500,
        mineru_mode: str | None = None,
        **extra: Any,
    ) -> ParsedDocument:
        results = self.parse_batch(
            [(file_path, output_dir)],
            file_type=file_type,
            max_file_size_mb=max_file_size_mb,
            mineru_mode=mineru_mode,
            fail_fast=True,
            **extra,
        )
        if not results:
            raise DocumentParseError("parse_batch returned empty for single-file input")
        doc = results[0]
        if isinstance(doc, DocumentParseError):
            raise doc
        return doc
