"""
Regulatory document parsers — PDF, HTML, structured feeds.

Each parser extracts plain text and structured metadata from a raw
byte payload. Parsers are stateless and return a ParseResult; the
ingestion pipeline applies the result to the document record.

Production integrations
───────────────────────
  PDF    → pdfminer.six / PyMuPDF (graceful fallback to placeholder)
  HTML   → html.parser from stdlib (no external dependency)
  JSON / XML feeds → stdlib json / xml.etree

All parsers are synchronous; async wrapping is handled by the pipeline.
"""

from __future__ import annotations

import html
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing      import Any, Optional

log = logging.getLogger("evidentrx.regulatory.ingestion.parsers")


@dataclass
class ParseResult:
    """Output of a parser run against raw document bytes."""
    success:       bool
    raw_text:      str                = ""
    title:         Optional[str]      = None
    summary:       Optional[str]      = None
    key_changes:   list[str]          = field(default_factory=list)
    metadata:      dict[str, Any]     = field(default_factory=dict)
    error:         Optional[str]      = None
    page_count:    Optional[int]      = None
    word_count:    int                = 0

    def __post_init__(self) -> None:
        if self.raw_text:
            self.word_count = len(self.raw_text.split())


class PDFParser:
    """
    Extracts text from PDF regulatory documents.

    Falls back gracefully when pdfminer is not installed — the raw
    bytes are decoded as UTF-8 (best-effort) and a warning is logged.
    This ensures the ingestion pipeline never hard-fails on missing
    optional dependencies.
    """

    def parse(self, raw: bytes, filename: str = "") -> ParseResult:
        try:
            from pdfminer.high_level import extract_text
            import io
            text = extract_text(io.BytesIO(raw))
            title = self._extract_title(text, filename)
            return ParseResult(
                success    = True,
                raw_text   = text,
                title      = title,
                summary    = self._first_paragraph(text),
                key_changes = self._extract_key_changes(text),
                metadata   = {"parser": "pdfminer"},
            )
        except ImportError:
            log.warning("PDFParser: pdfminer not installed; falling back to text decode")
        except Exception as exc:
            return ParseResult(success=False, error=f"PDF parse error: {exc}")

        # Fallback
        try:
            text = raw.decode("utf-8", errors="replace")
            return ParseResult(
                success   = True,
                raw_text  = text,
                title     = filename,
                metadata  = {"parser": "fallback_utf8"},
            )
        except Exception as exc:
            return ParseResult(success=False, error=str(exc))

    @staticmethod
    def _extract_title(text: str, filename: str) -> str:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return lines[0][:200] if lines else filename

    @staticmethod
    def _first_paragraph(text: str) -> str:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        return paragraphs[0][:500] if paragraphs else ""

    @staticmethod
    def _extract_key_changes(text: str) -> list[str]:
        """Heuristic: lines starting with bullet markers or numbered items."""
        pattern = re.compile(r"^\s*(?:[•·▪\-\*]|\d+[.):])\s+(.+)", re.MULTILINE)
        matches = pattern.findall(text)
        return [m.strip() for m in matches[:10]]


class HTMLParser:
    """Extracts text and metadata from HTML regulatory pages."""

    def parse(self, raw: bytes, url: str = "") -> ParseResult:
        try:
            import html.parser as _hp

            class _Extractor(_hp.HTMLParser):
                def __init__(self) -> None:
                    super().__init__()
                    self._text: list[str] = []
                    self._title          = ""
                    self._in_title       = False
                    self._skip_tags      = {"script", "style", "noscript"}
                    self._current_skip   = False

                def handle_starttag(self, tag: str, attrs: list) -> None:
                    if tag == "title":
                        self._in_title = True
                    if tag in self._skip_tags:
                        self._current_skip = True

                def handle_endtag(self, tag: str) -> None:
                    if tag == "title":
                        self._in_title = False
                    if tag in self._skip_tags:
                        self._current_skip = False

                def handle_data(self, data: str) -> None:
                    if self._in_title:
                        self._title = data.strip()
                    elif not self._current_skip:
                        stripped = data.strip()
                        if stripped:
                            self._text.append(stripped)

            extractor = _Extractor()
            extractor.feed(raw.decode("utf-8", errors="replace"))
            text = "\n".join(extractor._text)
            return ParseResult(
                success   = True,
                raw_text  = text,
                title     = extractor._title or url,
                summary   = text[:500],
                metadata  = {"parser": "html_stdlib", "source_url": url},
            )
        except Exception as exc:
            return ParseResult(success=False, error=f"HTML parse error: {exc}")


class StructuredFeedParser:
    """
    Parses JSON or XML regulatory feeds into ParseResult.

    Expected JSON schema (flexible, best-effort):
    {
      "title": str,
      "summary": str,
      "effective_date": str,
      "content": str,
      "changes": [str, ...]
    }
    """

    def parse_json(self, raw: bytes) -> ParseResult:
        try:
            data = json.loads(raw)
            text = data.get("content", "") or json.dumps(data, indent=2)
            return ParseResult(
                success    = True,
                raw_text   = text,
                title      = data.get("title"),
                summary    = data.get("summary", text[:400]),
                key_changes = data.get("changes", []),
                metadata   = {
                    "parser":         "json_feed",
                    "effective_date": data.get("effective_date"),
                    "document_number":data.get("document_number"),
                },
            )
        except Exception as exc:
            return ParseResult(success=False, error=f"JSON feed parse error: {exc}")

    def parse_xml(self, raw: bytes) -> ParseResult:
        try:
            root = ET.fromstring(raw)
            title   = root.findtext("title") or root.findtext(".//title") or ""
            content = " ".join(
                (el.text or "") for el in root.iter() if el.text
            )
            return ParseResult(
                success  = True,
                raw_text = content,
                title    = title,
                summary  = content[:400],
                metadata = {"parser": "xml_feed"},
            )
        except Exception as exc:
            return ParseResult(success=False, error=f"XML feed parse error: {exc}")


class PlainTextParser:
    """Handles pre-extracted plain-text regulatory documents."""

    def parse(self, raw: bytes) -> ParseResult:
        try:
            text = raw.decode("utf-8", errors="replace")
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            title = lines[0][:200] if lines else ""
            return ParseResult(
                success   = True,
                raw_text  = text,
                title     = title,
                summary   = "\n".join(lines[1:4]),
                metadata  = {"parser": "plain_text"},
            )
        except Exception as exc:
            return ParseResult(success=False, error=str(exc))
