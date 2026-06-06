"""Graceful, user-facing error reporting (M8.1).

A single place that turns *any* exception into a calm, **speakable** message —
so a traceback never reaches the user — and (optionally) consults the M7.3
:class:`~mimosa.tasks.error_learner.ErrorFixLearner` to surface a fix that
worked before.

Design goals (consistent with the rest of MimOSA):

* **Never raise.** Every public method is defensive; if anything goes wrong
  while *reporting* an error we fall back to a generic message.
* **Privacy-first.** Only the exception *type* and a normalised signature are
  used for learning; raw messages are never sent anywhere off-device, and the
  learner is entirely local (and opt-in).
* **No I/O at import.** Pure logic; the optional learner is injected.

The reporter is deliberately tiny and dependency-free so it can be used from
the voice loop, the router, background-task handlers, and the UI alike.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Friendly, speakable phrasing per error family
# ---------------------------------------------------------------------------
#: Generic, reassuring fallback used when nothing more specific matches.
GENERIC_MESSAGE = "Sorry, something went wrong while I was handling that."

#: Map well-known exception *types* (by name, to avoid importing optional
#: modules) to a calm, speakable, jargon-free explanation. Keyed by the
#: ``type(exc).__name__`` so it works for built-ins and third-party errors.
_MESSAGE_BY_TYPE = {
    "FileNotFoundError": "I couldn't find that file.",
    "IsADirectoryError": "That's a folder, not a file I can open.",
    "NotADirectoryError": "That path isn't a folder.",
    "PermissionError": "I don't have permission to do that.",
    "FileExistsError": "That already exists, so I left it alone.",
    "TimeoutError": "That took too long, so I stopped waiting.",
    "ConnectionError": "I couldn't reach the network just now.",
    "ConnectionResetError": "The connection dropped before I finished.",
    "ConnectionRefusedError": "The service refused the connection.",
    "InterruptedError": "That was interrupted before it finished.",
    "MemoryError": "I ran low on memory and had to stop.",
    "NotImplementedError": "I can't do that part yet.",
    "ValueError": "That didn't look quite right, so I couldn't continue.",
    "KeyError": "I was missing a piece of information I needed.",
    "OSError": "The system wouldn't let me complete that.",
}

#: Substring hints checked against the lowercased exception message when the
#: type alone isn't specific enough. First match wins.
_MESSAGE_BY_KEYWORD = (
    ("no space left", "Your disk is full, so I couldn't finish."),
    ("disk quota", "Your disk quota is full, so I couldn't finish."),
    ("timed out", "That took too long, so I stopped waiting."),
    ("connection", "I had trouble reaching the network."),
    ("permission denied", "I don't have permission to do that."),
    ("not found", "I couldn't find what I needed for that."),
    ("unauthorized", "I'm not authorised to do that."),
    ("api key", "That needs an API key I don't have configured."),
)


@dataclass
class ErrorReport:
    """The outcome of handling one error.

    Attributes:
        message: The calm, speakable, user-facing message (never a traceback).
        category: A short machine-readable family (e.g. ``"file"``, ``"network"``).
        signature: The normalised error signature used for learning.
        suggestion: A previously-successful fix, if the learner knew one.
        exc_type: The exception class name (for logs/metadata, not for users).
    """

    message: str
    category: str = "general"
    signature: str = ""
    suggestion: Optional[str] = None
    exc_type: str = ""

    def spoken(self) -> str:
        """Full message to speak/print, appending a known fix when available."""
        if self.suggestion:
            return f"{self.message} Last time, this was fixed by: {self.suggestion}."
        return self.message


#: Coarse category per exception family, for metrics/UX (not shown verbatim).
_CATEGORY_BY_TYPE = {
    "FileNotFoundError": "file",
    "IsADirectoryError": "file",
    "NotADirectoryError": "file",
    "FileExistsError": "file",
    "PermissionError": "permission",
    "TimeoutError": "network",
    "ConnectionError": "network",
    "ConnectionResetError": "network",
    "ConnectionRefusedError": "network",
    "MemoryError": "resource",
    "OSError": "system",
    "NotImplementedError": "unsupported",
}


def friendly_message(exc: BaseException) -> str:
    """Return a calm, speakable message for ``exc`` (never a traceback)."""
    try:
        # Keyword hints win first: they're more specific than a broad type like
        # OSError (e.g. "No space left on device" -> a disk-full message).
        text = str(exc).lower()
        for needle, msg in _MESSAGE_BY_KEYWORD:
            if needle in text:
                return msg
        name = type(exc).__name__
        if name in _MESSAGE_BY_TYPE:
            return _MESSAGE_BY_TYPE[name]
    except Exception:  # pragma: no cover - reporting must never raise
        pass
    return GENERIC_MESSAGE


def _category(exc: BaseException) -> str:
    return _CATEGORY_BY_TYPE.get(type(exc).__name__, "general")


@dataclass
class ErrorReporter:
    """Turn exceptions into friendly reports, with optional fix learning.

    Args:
        learner: An optional :class:`ErrorFixLearner` (M7.3). When present and
            available, the reporter will *suggest* known fixes and can *record*
            fixes that resolved an error. When ``None``, everything still works
            — just without suggestions.
        log: Whether to log the full exception (with traceback) for developers.
            User-facing output never includes the traceback regardless.
    """

    learner: Optional[object] = None
    log: bool = True
    _last_signature: str = field(default="", init=False, repr=False)

    def report(self, exc: BaseException, *, context: str = "") -> ErrorReport:
        """Build an :class:`ErrorReport` for ``exc``.

        Logs the developer-facing traceback (when ``log``), composes a
        speakable message, and—if a learner is wired—looks up a known fix.
        Never raises.
        """
        try:
            if self.log:
                logger.error(
                    "Handled error%s: %s: %s",
                    f" during {context}" if context else "",
                    type(exc).__name__, exc,
                    exc_info=True,
                )
        except Exception:  # pragma: no cover
            pass

        message = friendly_message(exc)
        category = _category(exc)
        raw = f"{type(exc).__name__}: {exc}"
        signature = ""
        suggestion = None
        try:
            from mimosa.tasks.error_learner import normalize_error

            signature = normalize_error(raw)
        except Exception:  # pragma: no cover - normaliser is defensive
            signature = ""
        self._last_signature = signature

        learner = self.learner
        if learner is not None:
            try:
                if getattr(learner, "available", False):
                    sug = learner.suggest_fix(raw)
                    if sug is not None:
                        suggestion = getattr(sug, "fix", None) or None
            except Exception:  # pragma: no cover - suggestions are best-effort
                logger.debug("Fix suggestion failed", exc_info=True)

        return ErrorReport(
            message=message,
            category=category,
            signature=signature,
            suggestion=suggestion,
            exc_type=type(exc).__name__,
        )

    def record_fix(self, error: str, fix: str) -> bool:
        """Record that ``fix`` resolved ``error`` (delegates to the learner).

        Returns ``True`` if recorded, ``False`` when no learner is available.
        Never raises.
        """
        learner = self.learner
        if learner is None:
            return False
        try:
            if not getattr(learner, "available", False):
                return False
            learner.record_fix(error, fix)
            return True
        except Exception:  # pragma: no cover - best-effort
            logger.debug("record_fix failed", exc_info=True)
            return False
