"""Source classification and balanced-perspective labeling (M6.1).

When MimOSA researches a topic it deliberately pulls from *across the spectrum*
rather than echoing a single viewpoint. This module is the vocabulary for that:

* :class:`SourceCategory` -- the kinds of source we recognise (mainstream news,
  alternative media, social platforms, video, think-tanks, academic, official).
* :class:`Source` -- one retrieved result, enriched with its category and a
  human-readable *perspective label*.
* :func:`classify_domain` / :func:`classify_url` -- map a host to a category
  using a curated, fully-offline domain table plus suffix heuristics
  (``.gov`` -> official, ``.edu`` -> academic, ...).
* :func:`summarize_perspectives` -- given a set of sources, report which
  perspectives are represented and which are missing, so the synthesizer can be
  explicit about balance (and call out blind spots).

Everything here is pure, local, and deterministic -- no network, no LLM. The
domain table is intentionally small and editable; it is a *heuristic aid for
balance*, not an authoritative or political judgement of any outlet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse


class SourceCategory(str, Enum):
    """A coarse, balance-oriented category for a source.

    These are *structural* buckets (what kind of outlet), chosen so a research
    answer can show a spread of perspectives. They are not a measure of
    credibility or political lean.
    """

    MAINSTREAM = "mainstream"      # established news wires / major outlets
    ALTERNATIVE = "alternative"    # independent / non-mainstream media, blogs
    SOCIAL = "social"              # social platforms & forums
    VIDEO = "video"                # video-first platforms
    THINK_TANK = "think_tank"      # policy institutes / advocacy research
    ACADEMIC = "academic"          # universities, journals, preprints
    OFFICIAL = "official"          # government / intergovernmental / standards
    REFERENCE = "reference"        # encyclopaedic / reference works
    OTHER = "other"                # uncategorised

    @property
    def label(self) -> str:
        """A short human-friendly label for the category."""
        return PERSPECTIVE_LABELS.get(self, self.value.replace("_", " ").title())


#: Human-readable perspective labels per category, used in synthesis output.
PERSPECTIVE_LABELS: Dict["SourceCategory", str] = {
    SourceCategory.MAINSTREAM: "Mainstream media",
    SourceCategory.ALTERNATIVE: "Alternative / independent media",
    SourceCategory.SOCIAL: "Social media & forums",
    SourceCategory.VIDEO: "Video platforms",
    SourceCategory.THINK_TANK: "Think tanks & policy institutes",
    SourceCategory.ACADEMIC: "Academic & research",
    SourceCategory.OFFICIAL: "Official / government",
    SourceCategory.REFERENCE: "Reference works",
    SourceCategory.OTHER: "Other sources",
}


# --- Curated, offline domain -> category table --------------------------------
# Small and intentionally non-exhaustive. Matching is by registered-domain
# suffix, so "www.bbc.co.uk" and "bbc.co.uk" both match "bbc.co.uk".
_DOMAIN_TABLE: Dict[str, SourceCategory] = {
    # Mainstream news / wires
    "reuters.com": SourceCategory.MAINSTREAM,
    "apnews.com": SourceCategory.MAINSTREAM,
    "bbc.com": SourceCategory.MAINSTREAM,
    "bbc.co.uk": SourceCategory.MAINSTREAM,
    "nytimes.com": SourceCategory.MAINSTREAM,
    "washingtonpost.com": SourceCategory.MAINSTREAM,
    "theguardian.com": SourceCategory.MAINSTREAM,
    "wsj.com": SourceCategory.MAINSTREAM,
    "cnn.com": SourceCategory.MAINSTREAM,
    "nbcnews.com": SourceCategory.MAINSTREAM,
    "foxnews.com": SourceCategory.MAINSTREAM,
    "aljazeera.com": SourceCategory.MAINSTREAM,
    "bloomberg.com": SourceCategory.MAINSTREAM,
    "ft.com": SourceCategory.MAINSTREAM,
    "npr.org": SourceCategory.MAINSTREAM,
    "cnbc.com": SourceCategory.MAINSTREAM,
    "abcnews.go.com": SourceCategory.MAINSTREAM,
    "cbsnews.com": SourceCategory.MAINSTREAM,
    "economist.com": SourceCategory.MAINSTREAM,
    # Alternative / independent media & blog platforms
    "substack.com": SourceCategory.ALTERNATIVE,
    "medium.com": SourceCategory.ALTERNATIVE,
    "theintercept.com": SourceCategory.ALTERNATIVE,
    "vox.com": SourceCategory.ALTERNATIVE,
    "thedailybeast.com": SourceCategory.ALTERNATIVE,
    "breitbart.com": SourceCategory.ALTERNATIVE,
    "motherjones.com": SourceCategory.ALTERNATIVE,
    "reason.com": SourceCategory.ALTERNATIVE,
    "wordpress.com": SourceCategory.ALTERNATIVE,
    "blogspot.com": SourceCategory.ALTERNATIVE,
    # Social platforms & forums
    "twitter.com": SourceCategory.SOCIAL,
    "x.com": SourceCategory.SOCIAL,
    "reddit.com": SourceCategory.SOCIAL,
    "facebook.com": SourceCategory.SOCIAL,
    "instagram.com": SourceCategory.SOCIAL,
    "linkedin.com": SourceCategory.SOCIAL,
    "mastodon.social": SourceCategory.SOCIAL,
    "news.ycombinator.com": SourceCategory.SOCIAL,
    "quora.com": SourceCategory.SOCIAL,
    "stackexchange.com": SourceCategory.SOCIAL,
    "stackoverflow.com": SourceCategory.SOCIAL,
    "threads.net": SourceCategory.SOCIAL,
    "tiktok.com": SourceCategory.SOCIAL,
    # Video-first platforms
    "youtube.com": SourceCategory.VIDEO,
    "youtu.be": SourceCategory.VIDEO,
    "vimeo.com": SourceCategory.VIDEO,
    "rumble.com": SourceCategory.VIDEO,
    "dailymotion.com": SourceCategory.VIDEO,
    "twitch.tv": SourceCategory.VIDEO,
    # Think tanks & policy institutes
    "brookings.edu": SourceCategory.THINK_TANK,
    "heritage.org": SourceCategory.THINK_TANK,
    "rand.org": SourceCategory.THINK_TANK,
    "cato.org": SourceCategory.THINK_TANK,
    "pewresearch.org": SourceCategory.THINK_TANK,
    "carnegieendowment.org": SourceCategory.THINK_TANK,
    "cfr.org": SourceCategory.THINK_TANK,
    "csis.org": SourceCategory.THINK_TANK,
    "aei.org": SourceCategory.THINK_TANK,
    "chathamhouse.org": SourceCategory.THINK_TANK,
    # Academic / research
    "arxiv.org": SourceCategory.ACADEMIC,
    "nature.com": SourceCategory.ACADEMIC,
    "science.org": SourceCategory.ACADEMIC,
    "sciencedirect.com": SourceCategory.ACADEMIC,
    "springer.com": SourceCategory.ACADEMIC,
    "ncbi.nlm.nih.gov": SourceCategory.ACADEMIC,
    "pubmed.ncbi.nlm.nih.gov": SourceCategory.ACADEMIC,
    "jstor.org": SourceCategory.ACADEMIC,
    "researchgate.net": SourceCategory.ACADEMIC,
    "ssrn.com": SourceCategory.ACADEMIC,
    "ieee.org": SourceCategory.ACADEMIC,
    "acm.org": SourceCategory.ACADEMIC,
    # Reference works
    "wikipedia.org": SourceCategory.REFERENCE,
    "britannica.com": SourceCategory.REFERENCE,
    "merriam-webster.com": SourceCategory.REFERENCE,
    "investopedia.com": SourceCategory.REFERENCE,
    "stanford.edu/entries": SourceCategory.REFERENCE,  # plato.stanford handled below
    "plato.stanford.edu": SourceCategory.REFERENCE,
}

#: TLD / suffix heuristics applied when no explicit domain entry matches.
_SUFFIX_RULES = (
    (".gov", SourceCategory.OFFICIAL),
    (".gov.uk", SourceCategory.OFFICIAL),
    (".mil", SourceCategory.OFFICIAL),
    (".int", SourceCategory.OFFICIAL),
    (".edu", SourceCategory.ACADEMIC),
    (".ac.uk", SourceCategory.ACADEMIC),
)


def _registered_host(domain_or_url: str) -> str:
    """Return a lowercased host with scheme/path/port stripped.

    Accepts either a bare host (``"BBC.co.uk"``) or a full URL
    (``"https://www.bbc.co.uk/news"``).
    """
    if not domain_or_url:
        return ""
    text = domain_or_url.strip()
    if "//" in text or text.startswith("http"):
        parsed = urlparse(text if "//" in text else "//" + text)
        host = parsed.netloc or parsed.path
    else:
        # bare host (possibly with a path appended)
        host = text.split("/", 1)[0]
    host = host.split("@")[-1]      # strip any userinfo
    host = host.split(":")[0]       # strip port
    return host.strip().lower().rstrip(".")


def classify_domain(domain: str) -> SourceCategory:
    """Classify a host/domain into a :class:`SourceCategory`.

    Matching strategy (all offline):

    1. Exact or suffix match against the curated domain table (so
       ``news.bbc.co.uk`` matches ``bbc.co.uk``).
    2. TLD/suffix rules (``.gov`` -> official, ``.edu`` -> academic, ...).
    3. Fallback to :attr:`SourceCategory.OTHER`.
    """
    host = _registered_host(domain)
    if not host:
        return SourceCategory.OTHER

    # 1. Curated table: exact, then suffix (label-boundary) match.
    if host in _DOMAIN_TABLE:
        return _DOMAIN_TABLE[host]
    for known, category in _DOMAIN_TABLE.items():
        if host == known or host.endswith("." + known):
            return category

    # 2. Suffix / TLD heuristics.
    for suffix, category in _SUFFIX_RULES:
        if host.endswith(suffix):
            return category

    return SourceCategory.OTHER


def classify_url(url: str) -> SourceCategory:
    """Classify a full URL into a :class:`SourceCategory` (see
    :func:`classify_domain`)."""
    return classify_domain(url)


def perspective_label(category: SourceCategory) -> str:
    """Return the human-readable perspective label for ``category``."""
    return PERSPECTIVE_LABELS.get(category, category.value.replace("_", " ").title())


@dataclass
class Source:
    """A single retrieved source, enriched for balanced synthesis.

    Attributes:
        title: The result title/headline.
        url: The source URL.
        snippet: A short excerpt/description of the content.
        category: The :class:`SourceCategory` (auto-derived from ``url`` if not
            supplied).
        domain: The registered host (auto-derived from ``url``).
        published: Optional published date string, if known.
        rank: Original search rank (0-based), useful for tie-breaking.
        metadata: Free-form extra fields.
    """

    title: str
    url: str = ""
    snippet: str = ""
    category: Optional[SourceCategory] = None
    domain: str = ""
    published: Optional[str] = None
    rank: int = 0
    metadata: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.domain:
            self.domain = _registered_host(self.url)
        if self.category is None:
            self.category = classify_domain(self.url or self.domain)

    @property
    def perspective(self) -> str:
        """The human-readable perspective label for this source."""
        return perspective_label(self.category or SourceCategory.OTHER)

    @property
    def text(self) -> str:
        """Title + snippet, the text used for ranking / synthesis."""
        parts = [p for p in (self.title, self.snippet) if p]
        return " — ".join(parts)

    def to_dict(self) -> Dict[str, object]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "category": (self.category or SourceCategory.OTHER).value,
            "domain": self.domain,
            "published": self.published,
            "rank": self.rank,
            "metadata": dict(self.metadata),
        }


def summarize_perspectives(
    sources: Iterable[Source],
    *,
    desired: Optional[Iterable[SourceCategory]] = None,
) -> Dict[str, object]:
    """Summarise which perspectives a set of sources represents.

    Args:
        sources: The sources to summarise.
        desired: Optional set of categories we *hoped* to see. Defaults to the
            "balance" set (mainstream, alternative, social, think-tank,
            academic, official). Any desired category not present is reported as
            a gap so the synthesis can be honest about blind spots.

    Returns:
        A dict with:
            * ``counts``: ``{category_value: n}`` for present categories.
            * ``present``: ordered list of present category values.
            * ``missing``: ordered list of desired-but-absent category values.
            * ``labels``: ``{category_value: human label}``.
            * ``diversity``: number of distinct categories present.
            * ``total``: total source count.
    """
    src_list = list(sources)
    counts: Dict[str, int] = {}
    for s in src_list:
        cat = (s.category or SourceCategory.OTHER).value
        counts[cat] = counts.get(cat, 0) + 1

    if desired is None:
        desired_cats = [
            SourceCategory.MAINSTREAM,
            SourceCategory.ALTERNATIVE,
            SourceCategory.SOCIAL,
            SourceCategory.THINK_TANK,
            SourceCategory.ACADEMIC,
            SourceCategory.OFFICIAL,
        ]
    else:
        desired_cats = list(desired)

    present = [c for c in counts]
    missing = [c.value for c in desired_cats if counts.get(c.value, 0) == 0]
    labels = {c: perspective_label(SourceCategory(c)) for c in counts}

    return {
        "counts": counts,
        "present": present,
        "missing": missing,
        "labels": labels,
        "diversity": len(counts),
        "total": len(src_list),
    }
