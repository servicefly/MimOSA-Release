"""Check-for-updates support (M4.2).

Phase 3 left a "check for updates" placeholder on the About page. This module
implements the logic behind it -- semantic-version parsing/comparison and an
:class:`UpdateChecker` that compares the running version against the latest
published one.

Privacy & testability
----------------------
MimOSA is local-first and does **no** background phone-home. An update check
only happens when the user explicitly asks for it, and the network call is
fully **injectable**: :class:`UpdateChecker` takes a ``fetcher`` callable that
returns the latest release info. The default fetcher queries the public GitHub
*releases* API, but tests inject a fake fetcher so nothing here touches the
network. The version-parsing helpers are pure functions.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: Default GitHub releases endpoint for the project.
DEFAULT_RELEASES_URL = "https://api.github.com/repos/servicefly/MimOSA/releases/latest"

#: A parsed semantic version: (major, minor, patch, prerelease-key).
#: ``prerelease-key`` is an empty tuple for final releases (which sort *after*
#: any pre-release of the same x.y.z, matching SemVer precedence).
_VERSION_RE = re.compile(
    r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+]([0-9A-Za-z.\-]+))?\s*$"
)


def parse_version(value: str) -> Optional[Tuple[int, int, int, tuple]]:
    """Parse a (loose) semantic version string into a comparable tuple.

    Accepts an optional leading ``v``, missing minor/patch (treated as 0), and
    an optional ``-prerelease`` / ``+build`` suffix. Returns ``None`` if the
    string is not a recognisable version.
    """
    if value is None:
        return None
    match = _VERSION_RE.match(str(value))
    if not match:
        return None
    major = int(match.group(1))
    minor = int(match.group(2) or 0)
    patch = int(match.group(3) or 0)
    pre = match.group(4) or ""
    return (major, minor, patch, _prerelease_key(pre))


def _prerelease_key(pre: str) -> tuple:
    """Build a SemVer-ish precedence key for a pre-release/build suffix.

    A final release (empty ``pre``) must sort *higher* than any pre-release of
    the same ``x.y.z``. We encode that by giving finals a leading sentinel of
    ``1`` and pre-releases ``0``; identifiers then compare numerically when
    numeric, else lexically.
    """
    if not pre:
        return (1,)  # final release sorts after pre-releases
    parts: list = [0]
    for ident in pre.split("."):
        if ident.isdigit():
            parts.append((0, int(ident)))  # numeric identifiers sort low
        else:
            parts.append((1, ident))
    return tuple(parts)


def compare_versions(a: str, b: str) -> int:
    """Return -1 if ``a`` < ``b``, 0 if equal, 1 if ``a`` > ``b``.

    Unparseable versions are treated as ``(0, 0, 0)`` finals so a comparison
    never raises.
    """
    pa = parse_version(a) or (0, 0, 0, _prerelease_key(""))
    pb = parse_version(b) or (0, 0, 0, _prerelease_key(""))
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0


def is_newer(candidate: str, current: str) -> bool:
    """True if ``candidate`` is a strictly newer version than ``current``."""
    return compare_versions(candidate, current) > 0


@dataclass
class UpdateInfo:
    """The outcome of an update check."""

    current_version: str
    latest_version: Optional[str] = None
    update_available: bool = False
    url: str = ""
    notes: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """True if the check completed without error (regardless of result)."""
        return self.error is None

    def summary(self) -> str:
        """A short, human/speakable summary of the check."""
        if self.error:
            return f"Couldn't check for updates: {self.error}"
        if self.update_available and self.latest_version:
            return (
                f"An update is available: {self.latest_version} "
                f"(you have {self.current_version})."
            )
        return f"You're up to date (version {self.current_version})."


def _default_fetcher(url: str, timeout: float) -> Dict[str, Any]:  # pragma: no cover - network
    """Fetch the latest release JSON from GitHub. Never imported at module load."""
    import json
    import urllib.request

    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "MimOSA"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_latest(payload: Any) -> Tuple[Optional[str], str, str]:
    """Pull (version, url, notes) out of a fetcher payload.

    Accepts either a GitHub release dict (``tag_name``/``name``/``html_url``/
    ``body``) or a plain version string.
    """
    if isinstance(payload, str):
        return payload.strip() or None, "", ""
    if isinstance(payload, dict):
        version = (
            payload.get("tag_name")
            or payload.get("version")
            or payload.get("name")
            or ""
        )
        url = payload.get("html_url") or payload.get("url") or ""
        notes = payload.get("body") or payload.get("notes") or ""
        return (str(version).strip() or None, str(url), str(notes))
    return None, "", ""


class UpdateChecker:
    """Compares the running version with the latest published release.

    Args:
        current_version: The running version. Defaults to ``mimosa.__version__``.
        fetcher: Callable ``(url, timeout) -> payload`` returning the latest
            release info (dict or version string). Injected in tests; defaults
            to a GitHub releases fetch.
        url: Releases endpoint passed to the fetcher.
        timeout: Network timeout (seconds) passed to the fetcher.
    """

    def __init__(
        self,
        current_version: Optional[str] = None,
        fetcher: Optional[Callable[[str, float], Any]] = None,
        url: str = DEFAULT_RELEASES_URL,
        timeout: float = 5.0,
    ) -> None:
        if current_version is None:
            try:
                from mimosa import __version__ as current_version  # type: ignore
            except Exception:  # pragma: no cover - mimosa always importable here
                current_version = "0.0.0"
        self.current_version = str(current_version)
        self._fetcher = fetcher or _default_fetcher
        self.url = url
        self.timeout = timeout

    def check(self) -> UpdateInfo:
        """Run the check; never raises -- failures land in ``UpdateInfo.error``."""
        try:
            payload = self._fetcher(self.url, self.timeout)
        except Exception as exc:
            logger.info("Update check failed: %s", exc)
            return UpdateInfo(current_version=self.current_version, error=str(exc))

        latest, url, notes = _extract_latest(payload)
        if not latest:
            return UpdateInfo(
                current_version=self.current_version,
                error="no release information available",
            )

        available = is_newer(latest, self.current_version)
        return UpdateInfo(
            current_version=self.current_version,
            latest_version=latest,
            update_available=available,
            url=url,
            notes=notes,
        )
