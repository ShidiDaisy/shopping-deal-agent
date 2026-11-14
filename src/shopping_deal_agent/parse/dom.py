"""Thin helpers over Scrapling's Selector so parsing strategies stay readable."""
from __future__ import annotations

import re
from typing import Iterable

from scrapling.parser import Selector

_SCRIPT_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def make_selector(html: str) -> Selector:
    return Selector(html or "<html></html>")


def css_texts(page: Selector, selector: str) -> list[str]:
    try:
        values = page.css(f"{selector}::text").getall()
    except Exception:
        return []
    return [str(v).strip() for v in values if str(v).strip()]


def css_attrs(page: Selector, selector: str, attr: str) -> list[str]:
    try:
        values = page.css(f"{selector}::attr({attr})").getall()
    except Exception:
        return []
    return [str(v).strip() for v in values if str(v).strip()]


def first_text(page: Selector, selectors: Iterable[str]) -> str | None:
    for selector in selectors:
        for value in css_texts(page, selector):
            return value
    return None


def first_attr(page: Selector, selectors: Iterable[str], attr: str) -> str | None:
    for selector in selectors:
        for value in css_attrs(page, selector, attr):
            return value
    return None


def visible_text(page: Selector, limit: int = 60000) -> str:
    try:
        html = page.html_content
    except Exception:
        html = ""
    cleaned = _SCRIPT_RE.sub(" ", html)
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", cleaned)).strip()[:limit]


def size_elements(page: Selector, selectors: Iterable[str]) -> list[tuple[str, str]]:
    """Return (label, lowercased attribute fingerprint) for size widgets."""
    result: list[tuple[str, str]] = []
    for selector in selectors:
        try:
            elements = page.css(selector)
        except Exception:
            continue
        for element in elements:
            try:
                label = str(getattr(element, "text", "") or "").strip()
            except Exception:
                label = ""
            try:
                attributes = " ".join(str(v) for v in element.attrib.values()).lower()
            except Exception:
                attributes = ""
            if label or attributes:
                result.append((label, attributes))
    return result
