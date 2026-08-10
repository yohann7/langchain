"""Plain text, markup, JSON, HTML, and CSV parsing."""

from __future__ import annotations

import csv
from html.parser import HTMLParser
import io
import json
from pathlib import Path


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden_depth += 1
        elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.hidden_depth:
            self.hidden_depth -= 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def load_text(path: Path, suffix: str) -> str:
    if suffix == ".json":
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        return json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True)
    if suffix == ".csv":
        rows = csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig")))
        return "\n".join(" | ".join(cell.strip() for cell in row) for row in rows)
    if suffix in {".html", ".htm"}:
        parser = _VisibleTextParser()
        parser.feed(path.read_text(encoding="utf-8-sig"))
        return parser.text()
    return path.read_text(encoding="utf-8-sig")
