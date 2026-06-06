"""Web search & source aggregation (M6.1).

This module turns a research query into a de-duplicated, category-labelled list
of :class:`~mimosa.research.sources.Source` objects, ready for budgeting (M6.3)
and synthesis (M6.2).

Architecture
------------
* :class:`SearchResult` -- a *raw* result (title/url/snippet/rank) as returned
  by a backend, before category enrichment.
* :class:`SearchBackend` -- the pluggable contract. Two backends ship:
    * :class:`StaticBackend` -- returns injected results; **fully offline and
      deterministic**, used by tests and as a safe default when no network
      provider is configured.
    * :class:`DuckDuckGoBackend` -- a key-free HTML backend (DuckDuckGo's
      ``html.duckduckgo.com`` endpoint) using ``requests`` + ``bs4`` when
      available. Network/parse failures degrade to an empty list, never raise.
* :class:`SearchClient` -- the public entry point. It calls the backend,
  enriches results into :class:`Source` objects (auto-classifying their
  perspective category), de-duplicates by domain/URL, and optionally caps the
  number of sources *per category* to encourage a balanced spread.

Privacy
-------
Search is an **outbound** capability. Like the weather skill, it is opt-in and
degrades gracefully. Only the query text is sent to the search backend; MimOSA
adds no identifying data and uses a generic user agent. When no backend is
wired (the default in headless/offline contexts), :meth:`SearchClient.search`
returns an empty list and the engine reports that cleanly.
"""

from __future__ import annotations

import abc
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence
from urllib.parse import parse_qs, quote_plus, urlparse

from mimosa.research.sources import Source, SourceCategory, classify_url

logger = logging.getLogger("mimosa.research.search")

# Optional network stack. Absence => the DuckDuckGo backend is unavailable but
# the rest of the module (and StaticBackend) still works.
try:  # pragma: no cover - environmental
    import requests  # type: ignore

    HAS_REQUESTS = True
except Exception:  # pragma: no cover
    requests = None  # type: ignore
    HAS_REQUESTS = False

try:  # pragma: no cover - environmental
    from bs4 import BeautifulSoup  # type: ignore

    HAS_BS4 = True
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore
    HAS_BS4 = False


@dataclass
class SearchResult:
    """A raw search result before perspective enrichment.

    Attributes:
        title: Result title/headline.
        url: Result URL.
        snippet: Short excerpt/description.
        rank: 0-based position in the backend's result list.
    """

    title: str
    url: str = ""
    snippet: str = ""
    rank: int = 0

    def to_source(self) -> Source:
        """Convert to an enriched :class:`Source` (auto-classifies category)."""
        return Source(
            title=self.title,
            url=self.url,
            snippet=self.snippet,
            rank=self.rank,
        )


class SearchBackend(abc.ABC):
    """Abstract contract for a search backend.

    Implementations must never raise from :meth:`search`; on any failure they
    return an empty list so the research pipeline degrades gracefully.
    """

    #: Whether this backend requires network access.
    is_network = False

    @abc.abstractmethod
    def search(self, query: str, *, max_results: int = 10) -> List[SearchResult]:
        """Return up to ``max_results`` raw results for ``query``."""
        raise NotImplementedError

    def available(self) -> bool:
        """Whether the backend can currently run (deps present, etc.)."""
        return True


class StaticBackend(SearchBackend):
    """A deterministic, offline backend returning injected results.

    Used by tests and as the default when no network backend is configured.
    Accepts either a flat list of results (returned for any query) or a mapping
    of ``query -> results`` for query-specific fixtures.
    """

    is_network = False

    def __init__(
        self,
        results=None,
        *,
        by_query: Optional[Dict[str, Sequence]] = None,
    ) -> None:
        self._results = self._coerce(results) if results else []
        self._by_query = {
            q.strip().lower(): self._coerce(rs) for q, rs in (by_query or {}).items()
        }

    @staticmethod
    def _coerce(items) -> List[SearchResult]:
        out: List[SearchResult] = []
        for i, item in enumerate(items or []):
            if isinstance(item, SearchResult):
                if not item.rank:
                    item.rank = i
                out.append(item)
            elif isinstance(item, Source):
                out.append(
                    SearchResult(
                        title=item.title, url=item.url, snippet=item.snippet, rank=i
                    )
                )
            elif isinstance(item, dict):
                out.append(
                    SearchResult(
                        title=str(item.get("title", "")),
                        url=str(item.get("url", "")),
                        snippet=str(item.get("snippet", "")),
                        rank=int(item.get("rank", i)),
                    )
                )
            else:  # (title, url, snippet) tuple
                title = item[0] if len(item) > 0 else ""
                url = item[1] if len(item) > 1 else ""
                snippet = item[2] if len(item) > 2 else ""
                out.append(SearchResult(title=title, url=url, snippet=snippet, rank=i))
        return out

    def search(self, query: str, *, max_results: int = 10) -> List[SearchResult]:
        key = (query or "").strip().lower()
        results = self._by_query.get(key, self._results)
        return list(results[:max_results])


class DuckDuckGoBackend(SearchBackend):
    """Key-free web search via DuckDuckGo's HTML endpoint.

    Uses ``requests`` to fetch and ``bs4`` to parse (with a regex fallback). All
    failures (no network, timeout, parse error, missing deps) return ``[]`` --
    research then proceeds with whatever other sources exist, or reports that no
    sources were found.
    """

    is_network = True
    ENDPOINT = "https://html.duckduckgo.com/html/"

    def __init__(self, *, timeout: float = 8.0, session=None) -> None:
        self.timeout = timeout
        self._session = session

    def available(self) -> bool:
        return HAS_REQUESTS

    def search(self, query: str, *, max_results: int = 10) -> List[SearchResult]:
        if not HAS_REQUESTS or not (query or "").strip():
            if not HAS_REQUESTS:
                logger.info("DuckDuckGoBackend unavailable: 'requests' not installed.")
            return []
        try:
            html = self._fetch(query)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            logger.warning("Web search request failed: %s", exc)
            return []
        if not html:
            return []
        try:
            return self._parse(html, max_results=max_results)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            logger.warning("Could not parse search results: %s", exc)
            return []

    def _fetch(self, query: str) -> str:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; MimOSA-Research/1.0)"}
        data = {"q": query}
        if self._session is not None:
            resp = self._session.post(self.ENDPOINT, data=data, headers=headers, timeout=self.timeout)
        else:
            resp = requests.post(self.ENDPOINT, data=data, headers=headers, timeout=self.timeout)  # type: ignore
        if resp.status_code >= 400:
            logger.warning("Web search HTTP %s", resp.status_code)
            return ""
        return resp.text or ""

    @staticmethod
    def _clean_ddg_url(href: str) -> str:
        """DuckDuckGo wraps targets in a redirect (``/l/?uddg=<encoded>``)."""
        if not href:
            return ""
        if "duckduckgo.com/l/" in href or href.startswith("/l/"):
            parsed = urlparse(href if href.startswith("http") else "https:" + href if href.startswith("//") else "https://duckduckgo.com" + href)
            qs = parse_qs(parsed.query)
            target = qs.get("uddg", [""])[0]
            if target:
                return target
        if href.startswith("//"):
            return "https:" + href
        return href

    def _parse(self, html: str, *, max_results: int) -> List[SearchResult]:
        results: List[SearchResult] = []
        if HAS_BS4:
            soup = BeautifulSoup(html, "html.parser")
            for i, res in enumerate(soup.select(".result, .web-result")):
                if len(results) >= max_results:
                    break
                link = res.select_one("a.result__a")
                if not link:
                    continue
                title = link.get_text(" ", strip=True)
                url = self._clean_ddg_url(link.get("href", ""))
                snippet_el = res.select_one(".result__snippet")
                snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
                if title and url:
                    results.append(SearchResult(title=title, url=url, snippet=snippet, rank=len(results)))
            if results:
                return results
        # Regex fallback (also covers the no-bs4 case).
        pattern = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )
        for m in pattern.finditer(html):
            if len(results) >= max_results:
                break
            url = self._clean_ddg_url(m.group(1))
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if title and url:
                results.append(SearchResult(title=title, url=url, snippet="", rank=len(results)))
        return results


class SearchClient:
    """High-level search facade producing balanced, de-duplicated sources.

    Args:
        backend: The :class:`SearchBackend` to use. When ``None`` the client is
            in *offline* mode: :meth:`search` returns an empty list.
        max_results: Default cap on raw results requested from the backend.
        per_category_cap: Optional cap on how many sources to keep *per
            perspective category*, to avoid a single category dominating the
            evidence (encourages balance). ``None`` disables the cap.
    """

    def __init__(
        self,
        backend: Optional[SearchBackend] = None,
        *,
        max_results: int = 10,
        per_category_cap: Optional[int] = None,
    ) -> None:
        self.backend = backend
        self.max_results = max(1, int(max_results))
        self.per_category_cap = per_category_cap

    @property
    def online(self) -> bool:
        """Whether a usable backend is wired."""
        return self.backend is not None and self.backend.available()

    def search(
        self,
        query: str,
        *,
        max_results: Optional[int] = None,
        per_category_cap: Optional[int] = None,
    ) -> List[Source]:
        """Search and return enriched, de-duplicated :class:`Source` objects.

        Never raises: a missing/failing backend yields an empty list.
        """
        if not (query or "").strip():
            return []
        if self.backend is None:
            logger.info("SearchClient is offline (no backend); returning no sources.")
            return []
        limit = max_results or self.max_results
        try:
            raw = self.backend.search(query, max_results=limit)
        except Exception as exc:  # noqa: BLE001 - backends shouldn't raise, but be safe
            logger.warning("Search backend raised: %s", exc)
            return []

        sources = [r.to_source() if isinstance(r, SearchResult) else r for r in raw]
        sources = self._dedupe(sources)
        cap = per_category_cap if per_category_cap is not None else self.per_category_cap
        if cap is not None:
            sources = self._cap_per_category(sources, cap)
        # Re-rank sequentially after filtering for stable downstream ordering.
        for i, s in enumerate(sources):
            s.rank = i
        return sources

    @staticmethod
    def _dedupe(sources: List[Source]) -> List[Source]:
        """Drop duplicate URLs (and exact title+domain repeats)."""
        seen_url = set()
        seen_key = set()
        out: List[Source] = []
        for s in sources:
            url_key = (s.url or "").strip().rstrip("/").lower()
            title_key = ((s.title or "").strip().lower(), s.domain)
            if url_key and url_key in seen_url:
                continue
            if title_key in seen_key:
                continue
            if url_key:
                seen_url.add(url_key)
            seen_key.add(title_key)
            out.append(s)
        return out

    @staticmethod
    def _cap_per_category(sources: List[Source], cap: int) -> List[Source]:
        if cap <= 0:
            return list(sources)
        counts: Dict[SourceCategory, int] = {}
        out: List[Source] = []
        for s in sources:
            cat = s.category or SourceCategory.OTHER
            if counts.get(cat, 0) >= cap:
                continue
            counts[cat] = counts.get(cat, 0) + 1
            out.append(s)
        return out
