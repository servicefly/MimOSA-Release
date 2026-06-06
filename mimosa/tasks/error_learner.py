"""Local error-fix learning for MimOSA (M7.3).

When a background task (or any operation) fails, MimOSA shouldn't make the same
mistake forever. This module remembers, **entirely on-device**, which *fix*
resolved a given *kind* of error so that next time the same failure appears it
can suggest -- or auto-apply -- the remedy that worked before.

It is a thin, purpose-built layer over the M5.2
:class:`~mimosa.memory.preference_learner.PreferenceLearner`: errors are folded
into stable *signatures* (so "No such file: /home/a/x.txt" and
"No such file: /tmp/y.log" learn together), and each signature maps to candidate
fixes with confidence that grows as evidence accumulates.

Design principles
-----------------
* **Local & private.** All learning lives in the existing local preferences
  SQLite DB under a dedicated category. No telemetry; nothing leaves the device.
* **Reuses proven machinery.** Confidence scoring, explainability and
  forgetting all come from :class:`PreferenceLearner`; we only add error
  *normalisation* and a friendly API.
* **Headless & dependency-free.** Standard library only.
* **Graceful.** With no learner wired (or learning disabled) every method is a
  safe no-op / ``None``.
* **Deterministic to test.** Inject a ``:memory:`` learner; signatures are pure
  functions of the input string.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional

logger = logging.getLogger("mimosa.tasks.error_learner")

#: Preference-learner category under which error→fix associations are stored.
ERROR_LEARNING_CATEGORY = "error_fix"

#: Max length of a normalised signature (keeps keys bounded & comparable).
_MAX_SIGNATURE_LEN = 120

# Substitution rules that strip the *variable* parts of an error message so the
# stable *shape* remains. Order matters (most specific first).
_NORMALISERS = (
    (re.compile(r"0x[0-9a-fA-F]+"), "<hex>"),                 # memory addresses
    (re.compile(r"\b[0-9a-fA-F]{8,}\b"), "<hash>"),           # long hashes/ids
    (re.compile(r"(?:/[^/\s:'\"]+)+/?"), "<path>"),           # unix paths
    (re.compile(r"['\"][^'\"]*['\"]"), "<str>"),              # quoted literals
    (re.compile(r"\b\d+\b"), "<n>"),                          # bare numbers
    (re.compile(r"\s+"), " "),                                 # collapse spaces
)


def normalize_error(error: str) -> str:
    """Reduce an error message to a stable, comparable *signature*.

    Strips paths, quoted strings, numbers, hashes and addresses -- the parts
    that vary run-to-run -- leaving the error's recognisable shape. Returns an
    empty string for blank input.

    Examples:
        >>> normalize_error("FileNotFoundError: '/home/a/x.txt' missing (errno 2)")
        'filenotfounderror: <str> missing (errno <n>)'
    """
    if not error:
        return ""
    sig = str(error).strip().lower()
    for pattern, repl in _NORMALISERS:
        sig = pattern.sub(repl, sig)
    sig = sig.strip()
    if len(sig) > _MAX_SIGNATURE_LEN:
        sig = sig[:_MAX_SIGNATURE_LEN].rstrip()
    return sig


@dataclass
class FixSuggestion:
    """A learned fix for an error signature with its confidence.

    Attributes:
        signature: The normalised error signature this fix applies to.
        fix: The remedy that previously resolved the error.
        confidence: Learned confidence in ``[0, 1]``.
        count: How many times this fix was observed for the signature.
    """

    signature: str
    fix: str
    confidence: float
    count: int

    def to_dict(self) -> dict:
        return {
            "signature": self.signature,
            "fix": self.fix,
            "confidence": self.confidence,
            "count": self.count,
        }


class ErrorFixLearner:
    """Learn and recall which fixes resolve which errors -- all on-device.

    Args:
        preference_learner: An M5.2 ``PreferenceLearner`` providing the storage
            and confidence machinery. ``None`` disables learning (all methods
            become safe no-ops), keeping the feature optional and headless-safe.
        enabled: When ``False``, :meth:`record_fix` is a no-op while reads still
            work (mirrors the learner's own opt-out semantics).
        confidence_threshold: Minimum confidence for :meth:`suggest_fix` to
            surface a remedy as actionable.
    """

    def __init__(
        self,
        preference_learner=None,
        *,
        enabled: bool = True,
        confidence_threshold: float = 0.6,
    ) -> None:
        self._learner = preference_learner
        self.enabled = bool(enabled)
        self.confidence_threshold = float(confidence_threshold)

    @property
    def available(self) -> bool:
        """Whether a backing learner is wired (learning is possible)."""
        return self._learner is not None

    # -- learning ----------------------------------------------------------

    def record_fix(self, error: str, fix: str, *, weight: float = 1.0) -> bool:
        """Record that ``fix`` resolved ``error``. Returns ``True`` if stored.

        No-op (returns ``False``) when disabled, unwired, or either argument is
        blank / produces an empty signature.
        """
        if not (self.enabled and self._learner is not None):
            return False
        signature = normalize_error(error)
        fix = (fix or "").strip()
        if not signature or not fix:
            return False
        self._learner.observe(ERROR_LEARNING_CATEGORY, signature, fix, weight=weight)
        logger.debug("Recorded fix for signature %r", signature)
        return True

    # -- recall ------------------------------------------------------------

    def suggest_fix(
        self, error: str, *, threshold: Optional[float] = None
    ) -> Optional[FixSuggestion]:
        """Return the best learned fix for ``error`` if confident enough.

        ``threshold`` overrides :attr:`confidence_threshold` for this call.
        Returns ``None`` when no learner is wired, the signature is empty, or no
        candidate clears the confidence bar.
        """
        if self._learner is None:
            return None
        signature = normalize_error(error)
        if not signature:
            return None
        cut = self.confidence_threshold if threshold is None else float(threshold)
        pref = self._learner.predict(ERROR_LEARNING_CATEGORY, signature, threshold=cut)
        if pref is None:
            return None
        return FixSuggestion(
            signature=signature,
            fix=pref.value,
            confidence=pref.confidence,
            count=pref.count,
        )

    def candidates(self, error: str) -> List[FixSuggestion]:
        """Return all known fixes for ``error``'s signature, best first."""
        if self._learner is None:
            return []
        signature = normalize_error(error)
        if not signature:
            return []
        prefs = self._learner.get_preferences(ERROR_LEARNING_CATEGORY, signature)
        return [
            FixSuggestion(
                signature=signature, fix=p.value, confidence=p.confidence, count=p.count
            )
            for p in prefs
        ]

    def explain(self, error: str) -> str:
        """Return a human-readable explanation of what's known for an error."""
        signature = normalize_error(error)
        if not signature:
            return "No error signature to explain."
        if self._learner is None:
            return f"No fix learned for {signature!r} (learning unavailable)."
        cands = self.candidates(error)
        if not cands:
            return f"No fix learned yet for {signature!r}."
        lines = [f"Learned fixes for {signature!r}:"]
        for c in cands:
            lines.append(f"  • {c.fix}: seen {c.count}×, confidence {c.confidence:.0%}")
        return "\n".join(lines)

    def forget(self, error: str, fix: Optional[str] = None) -> int:
        """Forget learned fixes for an error signature. Returns rows removed."""
        if self._learner is None:
            return 0
        signature = normalize_error(error)
        if not signature:
            return 0
        return self._learner.forget(ERROR_LEARNING_CATEGORY, signature, fix)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"ErrorFixLearner(available={self.available}, enabled={self.enabled})"
