from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.document_reader import DocumentReader  # noqa: E402
from src.models.document import ParsedDocument  # noqa: E402
from src.parsers.base_parser import DocumentParseError  # noqa: E402
from src.utils.dependency_checker import (  # noqa: E402
    get_available_capability,
    is_markitdown_available,
    is_mineru_sdk_available,
    is_network_available,
    mineru_token_present,
    python_version_ok,
)
from src.utils.file_type_detector import (  # noqa: E402
    detect_file_type,
)


# ---------------------------------------------------------------------------
# 1. ParsedDocument dataclass helpers
# ---------------------------------------------------------------------------
class TestParsedDocument:
    def test_to_dict_roundtrip(self):
        table = [["c1", "c2"], ["v1", "v2"], ["v3", "v4"]]
        doc = ParsedDocument(
            file_path="/x/a.pdf",
            file_type="pdf",
            text="hello world\nline2",
            pages=["p1", "p2"],
            tables=[table],  # tables: list[tables] -> each table is 2D
            markdown="# Hello\n\nworld",
            metadata={"author": "tester"},
            parser_used="markitdown",
        )
        d = doc.to_dict()
        assert isinstance(d, dict)
        assert d["file_path"] == "/x/a.pdf"
        assert d["pages"] == ["p1", "p2"]
        assert d["tables"][0][1] == ["v1", "v2"]
        reconstructed = ParsedDocument(**d)
        assert reconstructed.text == doc.text

    def test_to_json_contains_chinese(self):
        doc = ParsedDocument(file_path="/测试.pdf", file_type="pdf", text="你好")
        j = doc.to_json(ensure_ascii=False)
        assert "你好" in j
        obj = json.loads(j)
        assert obj["file_type"] == "pdf"

    def test_preview_chars_truncation(self):
        long = "a" * 2000
        doc = ParsedDocument(file_path="x", file_type="txt", text=long)
        assert len(doc.preview_chars(500)) == 500 + len("...<truncated>")
        assert len(doc.preview_chars(5000)) == 2000

    def test_repr_short(self):
        doc = ParsedDocument(file_path="/tmp/longname.pdf", file_type="pdf", text="abc" * 1000)
        s = repr(doc)
        assert len(s) < 600
        assert "ParsedDocument(file_path=" in s
        assert "text_len=3000" in s


# ---------------------------------------------------------------------------
# 2. File type detection
# ---------------------------------------------------------------------------
class TestFileTypeDetector:
    @pytest.mark.parametrize(
        "name, expected",
        [
            ("a.PDF", "pdf"),
            ("report.docx", "docx"),
            ("data.XLSX", "xlsx"),
            ("old.DOC", "doc"),
            ("deck.pptx", "pptx"),
            ("sheet.xls", "xls"),
            ("photo.jpg", "image"),
            ("diagram.PNG", "image"),
            ("photo.webp", "image"),
            ("a.csv", "csv"),
            ("b.json", "json"),
            ("c.xml", "xml"),
            ("d.md", "md"),
            ("e.markdown", "md"),
            ("f.txt", "txt"),
            ("g.html", "html"),
            ("book.epub", "epub"),
            ("archive.zip", "zip"),
            ("something.unknownxyz", "unknown"),
        ],
    )
    def test_extension_based_detection(self, name, expected, tmp_path):
        f = tmp_path / name
        f.write_bytes(b"placeholder")
        # use_magic=False to force pure ext
        assert detect_file_type(f, use_magic=False) == expected

    def test_override_hint(self, tmp_path):
        f = tmp_path / "a.xyz"
        f.write_bytes(b"")
        assert detect_file_type(f, override="pdf") == "pdf"


# ---------------------------------------------------------------------------
# 3. Dependency checker (pure logic, verifiable via monkeypatch)
# ---------------------------------------------------------------------------
class TestDependencyChecker:
    def test_python_version(self):
        assert isinstance(python_version_ok(3, 12), bool)

    def test_mineru_token_present(self, monkeypatch):
        monkeypatch.delenv("MINERU_TOKEN", raising=False)
        assert mineru_token_present() is False
        monkeypatch.setenv("MINERU_TOKEN", "abc")
        assert mineru_token_present() is True

    def test_network_returns_bool(self):
        # Avoid real network I/O
        assert isinstance(is_network_available(timeout=0.001), bool)

    def test_capability_dict(self):
        cap = get_available_capability()
        for k in [
            "python_310_plus",
            "mineru_sdk",
            "mineru_token_present",
            "markitdown",
            "markitdown_ocr_plugin",
            "network",
        ]:
            assert k in cap
            assert isinstance(cap[k], bool)


# ---------------------------------------------------------------------------
# 4. Text parser basics (offline, zero heavy deps)
# ---------------------------------------------------------------------------
class TestTextParserBasics:
    def test_txt_basic(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("Hello world\n这是中文\nline 3", encoding="utf-8")
        reader = DocumentReader(prefer_mineru_sdk=False)
        doc = reader.read(f)
        assert doc.file_type == "txt"
        assert "Hello world" in doc.text
        assert "这是中文" in doc.text
        assert "text_parser" in doc.parser_used

    def test_md_writes_markdown_field(self, tmp_path):
        f = tmp_path / "note.md"
        f.write_text("# Title\n\npara **bold**\n\n- item1\n- item2")
        reader = DocumentReader(prefer_mineru_sdk=False)
        doc = reader.read(f)
        assert doc.markdown.startswith("# Title")
        assert "Title" in doc.text
        assert doc.parser_used.startswith("text_parser")

    def test_csv_extracts_tables(self, tmp_path):
        import csv

        f = tmp_path / "data.csv"
        rows = [
            ["name", "age", "city"],
            ["Alice", "30", "Beijing"],
            ["Bob", "25", "Shanghai"],
            ["Charlie", "35", "Guangzhou"],
        ]
        with open(f, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerows(rows)
        reader = DocumentReader(prefer_mineru_sdk=False)
        doc = reader.read(f)
        assert doc.file_type == "csv"
        assert len(doc.tables) >= 1
        header = doc.tables[0][0]
        assert "name" in header
        assert any("Alice" in str(row) for row in doc.tables[0])

    def test_json_pretty(self, tmp_path):
        f = tmp_path / "obj.json"
        f.write_text('{"a":1,"b":{"c":[1,2,3]},"name":"中文"}')
        reader = DocumentReader(prefer_mineru_sdk=False)
        doc = reader.read(f)
        # Pretty JSON should use 2-space indentation, newlines
        assert "\n" in doc.text
        assert '"name": "中文"' in doc.text
        assert "```json" in doc.markdown

    def test_gbk_encoding(self, tmp_path):
        f = tmp_path / "gbk.txt"
        f.write_bytes("中文GBK内容\n第二行".encode("gbk"))
        reader = DocumentReader(prefer_mineru_sdk=False)
        doc = reader.read(f)
        # chardet should detect gbk on a large-enough sample; at least it must not crash
        assert isinstance(doc.text, str)
        assert len(doc.text) > 0


# ---------------------------------------------------------------------------
# 5. DocumentReader errors + dispatcher
# ---------------------------------------------------------------------------
class TestDocumentReaderErrors:
    def test_file_not_found(self, tmp_path):
        reader = DocumentReader(prefer_mineru_sdk=False)
        with pytest.raises(DocumentParseError) as exc:
            reader.read(tmp_path / "notexist.pdf")
        assert "File not found" in str(exc.value)

    def test_file_type_hint_unknown_works_as_override(self, tmp_path):
        f = tmp_path / "weird.xyz"
        f.write_text("looks like plain text fallback")
        reader = DocumentReader(prefer_mineru_sdk=False)
        doc = reader.read(f, file_type_hint="md")
        assert doc.file_type == "md"
        assert "plain text fallback" in doc.text

    def test_python_version_strict_raise(self, monkeypatch, tmp_path):
        # Temporarily flip version_ok to False
        import src.document_reader as dr_mod

        monkeypatch.setattr(dr_mod, "python_version_ok", lambda: False)
        with pytest.raises(RuntimeError):
            DocumentReader(python_version_strict=True)

    def test_max_file_size(self, tmp_path, monkeypatch):
        # Build a 6MB file; then pass a 5MB reader cap to trigger size check
        f = tmp_path / "big.txt"
        f.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
        reader = DocumentReader(prefer_mineru_sdk=False, max_file_size_mb=5)
        with pytest.raises(DocumentParseError) as exc:
            reader.read(f)
        assert "too large" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# 6. Facade dispatcher mocks (no real mineru/markitdown calls)
# ---------------------------------------------------------------------------
class TestFacadeDispatcher:
    @staticmethod
    def _make_reader_with_mocks(monkeypatch, pdf_result, office_result, image_result, text_result):
        import src.document_reader as dr_mod

        class FakePdf:
            def parse(self, path, **kw):
                return pdf_result

        class FakeOffice:
            def parse(self, path, **kw):
                return office_result

        class FakeImage:
            def parse(self, path, **kw):
                return image_result

        class FakeText:
            def parse(self, path, **kw):
                return text_result

        monkeypatch.setattr(dr_mod, "is_mineru_sdk_available", lambda: False)
        monkeypatch.setattr(dr_mod, "is_markitdown_available", lambda: False)
        monkeypatch.setattr(dr_mod, "is_network_available", lambda timeout=2: False)

        reader = dr_mod.DocumentReader(prefer_mineru_sdk=False)
        reader._pdf = FakePdf()
        reader._office = FakeOffice()
        reader._image = FakeImage()
        reader._text = FakeText()
        reader._mineru = None
        reader._markitdown = None
        return reader

    def test_routes_pdf(self, tmp_path, monkeypatch):
        expected = ParsedDocument(str(tmp_path / "a.pdf"), "pdf", text="from_pdf")
        reader = self._make_reader_with_mocks(
            monkeypatch,
            expected,
            ParsedDocument("", "", text="office"),
            ParsedDocument("", "", text="image"),
            ParsedDocument("", "", text="text"),
        )
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF-1.4")
        got = reader.read(f)
        assert got.text == "from_pdf"

    def test_routes_office_docx(self, tmp_path, monkeypatch):
        expected = ParsedDocument(str(tmp_path / "d.docx"), "docx", text="docx_ok")
        reader = self._make_reader_with_mocks(
            monkeypatch,
            ParsedDocument("", "", text="pdf"),
            expected,
            ParsedDocument("", "", text="image"),
            ParsedDocument("", "", text="text"),
        )
        f = tmp_path / "d.docx"
        f.write_bytes(b"PK\x03\x04 placeholder")
        got = reader.read(f)
        assert got.text == "docx_ok"

    def test_routes_image_png(self, tmp_path, monkeypatch):
        expected = ParsedDocument(str(tmp_path / "x.png"), "image", text="png_ok")
        reader = self._make_reader_with_mocks(
            monkeypatch,
            ParsedDocument("", "", text="pdf"),
            ParsedDocument("", "", text="office"),
            expected,
            ParsedDocument("", "", text="text"),
        )
        f = tmp_path / "x.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\nplaceholder")
        got = reader.read(f)
        assert got.text == "png_ok"


# ---------------------------------------------------------------------------
# 7. Fallback chain (monkeypatch to simulate MinerU network failure)
# ---------------------------------------------------------------------------
class TestFallbackChain:
    def test_network_false_skips_mineru(self, monkeypatch, tmp_path):
        # Write a simple CSV (pure text path, no markitdown needed)
        f = tmp_path / "a.csv"
        f.write_text("a,b,c\n1,2,3\n4,5,6")
        monkeypatch.setattr(
            "src.utils.dependency_checker.is_network_available",
            lambda timeout=2: False,
        )
        reader = DocumentReader(prefer_mineru_sdk=True)
        # CSV goes through text_parser regardless; verify capability reports no network
        cap = reader.capability()
        assert cap["network"] is False
        doc = reader.read(f)
        assert len(doc.tables) == 1
        assert doc.parser_used.startswith("text_parser")

    def test_read_batch_fail_slow(self, tmp_path):
        f1 = tmp_path / "good.md"
        f1.write_text("hi")
        f2 = tmp_path / "missing.txt"
        f3 = tmp_path / "also_good.csv"
        f3.write_text("id,name\n1,alice")
        reader = DocumentReader(prefer_mineru_sdk=False)
        results = reader.read_batch([f1, f2, f3], fail_fast=False)
        assert len(results) == 3
        assert isinstance(results[0], ParsedDocument)
        assert isinstance(results[1], DocumentParseError)
        assert isinstance(results[2], ParsedDocument)


# ---------------------------------------------------------------------------
# 8. Integration (skipped on demand) — real markitdown / MinerU calls
# ---------------------------------------------------------------------------
def _markitdown_available() -> bool:
    try:
        return is_markitdown_available()
    except Exception:
        return False


@pytest.mark.skipif(not _markitdown_available(), reason="markitdown not installed")
class TestMarkItDownIntegration:
    def test_simple_xlsx_generated_via_pandas(self, tmp_path):
        try:
            import pandas as pd
            from markitdown import MarkItDown  # noqa: F401
        except ImportError:
            pytest.skip("pandas or markitdown missing")
        f = tmp_path / "smoke.xlsx"
        df1 = pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]})
        df2 = pd.DataFrame({"p": [10, 20], "q": [100, 200]})
        with pd.ExcelWriter(f) as w:
            df1.to_excel(w, sheet_name="S1", index=False)
            df2.to_excel(w, sheet_name="S2", index=False)
        reader = DocumentReader(prefer_mineru_sdk=False)
        doc = reader.read(f)
        assert len(doc.text) > 0 or len(doc.markdown) > 0
        # Office parser augments markitdown with pandas for extra table extraction
        assert doc.parser_used.endswith("markitdown") or "markitdown" in doc.parser_used


@pytest.mark.skipif(
    not (is_mineru_sdk_available() and is_network_available()),
    reason="mineru sdk or network unavailable",
)
class TestMinerUFlashIntegration:
    def test_small_txt_via_mineru_catch_all(self, tmp_path):
        # MinerU mainly targets PDF/Office/image; just check availability here.
        # If user has no token, avoid real PDF runs (would trigger large-file error)
        reader = DocumentReader(prefer_mineru_sdk=True)
        cap = reader.capability()
        assert cap["mineru_sdk"] is True


@pytest.mark.skipif(
    not mineru_token_present(),
    reason="MINERU_TOKEN not set, skip precision test",
)
def test_mineru_token_in_env():
    assert isinstance(os.environ.get("MINERU_TOKEN"), str)
    assert len(os.environ["MINERU_TOKEN"]) > 0
