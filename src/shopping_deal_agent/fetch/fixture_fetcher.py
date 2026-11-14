"""Offline transport for tests, demos and parser development.

Results are marked ``SourceKind.FIXTURE`` and can only produce alerts when
``Settings.allow_fixture_alerts`` is explicitly enabled (CLI forces dry-run).
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..clock import utcnow
from ..models import FetchResult, SourceKind


class FixtureFetcher:
    def __init__(
        self,
        html_by_url: Mapping[str, str],
        status_code: int = 200,
        blocked_reason: str | None = None,
    ) -> None:
        self._html_by_url = dict(html_by_url)
        self.status_code = status_code
        self.blocked_reason = blocked_reason

    @classmethod
    def from_file(cls, path: str | Path, url: str = "*") -> "FixtureFetcher":
        return cls({url: Path(path).read_text(encoding="utf-8")})

    def fetch(self, url: str) -> FetchResult:
        html = self._html_by_url.get(url)
        if html is None:
            html = self._html_by_url.get("*")
        if html is None:
            return FetchResult(
                url=url, final_url=url, status_code=None, html="",
                fetched_at=utcnow(), source_kind=SourceKind.FIXTURE,
                transport="fixture", error="no fixture registered for this URL",
            )
        return FetchResult(
            url=url,
            final_url=url,
            status_code=self.status_code,
            html=html,
            fetched_at=utcnow(),
            source_kind=SourceKind.FIXTURE,
            transport="fixture",
            from_cache=False,
            blocked_reason=self.blocked_reason,
        )
