"""High-level memory consolidation & maintenance for MimOSA (M4).

The low-level, dependency-free helpers in
:mod:`mimosa.memory.memory_consolidator` know how to collapse near-duplicate
texts and facts. This module wraps them in a friendly orchestrator,
:class:`MemoryConsolidator`, that performs the *housekeeping* a long-lived
companion needs:

* **merge duplicate facts** in the structured profile (e.g. "React" stored
  twice with slightly different phrasing),
* **detect contradictions** ("prefers a casual tone" vs "wants formal
  replies") and surface them for the user to resolve,
* run a **light** pass (cheap dedup, safe to do daily) or a **deep** pass
  (also analyses patterns / contradictions, intended weekly),
* be **user-triggerable** from Settings ("Clean up my profile").

Everything is best-effort and never raises: consolidation is housekeeping, so
on any error we simply leave memory as-is.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mimosa.memory.memory_consolidator import (
    consolidate_texts,
    text_similarity,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MemoryConsolidator",
    "ConsolidationReport",
    "Contradiction",
    "CONSOLIDATION_LIGHT",
    "CONSOLIDATION_DEEP",
]

CONSOLIDATION_LIGHT = "light"
CONSOLIDATION_DEEP = "deep"

#: Pairs of opposing preference concepts. If both sides appear in the profile
#: we flag a contradiction for the user to resolve. Lowercase, substring match.
_ANTONYM_GROUPS = (
    ("casual", "formal"),
    ("brief", "detailed"),
    ("concise", "verbose"),
    ("short answers", "long answers"),
    ("quiet", "chatty"),
    ("dark mode", "light mode"),
    ("morning person", "night owl"),
)


@dataclass
class Contradiction:
    """Two stored beliefs that appear to conflict."""

    field: str
    first: str
    second: str
    question: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "first": self.first,
            "second": self.second,
            "question": self.question,
        }


@dataclass
class ConsolidationReport:
    """Outcome of a consolidation pass (for logging / UI feedback)."""

    mode: str = CONSOLIDATION_LIGHT
    duplicates_removed: int = 0
    fields_touched: List[str] = field(default_factory=list)
    contradictions: List[Contradiction] = field(default_factory=list)
    ran_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "duplicates_removed": self.duplicates_removed,
            "fields_touched": list(self.fields_touched),
            "contradictions": [c.to_dict() for c in self.contradictions],
            "ran_at": self.ran_at,
        }

    @property
    def changed(self) -> bool:
        return self.duplicates_removed > 0


class MemoryConsolidator:
    """Orchestrate periodic / on-demand memory maintenance.

    Args:
        profile_manager: The :class:`ProfileManager` whose profile gets tidied.
        vector_store: Optional :class:`MemoryVectorStore` (reserved for future
            episodic pruning; currently only profile facts are consolidated).
        similarity: Optional custom similarity callable (defaults to the
            token-overlap :func:`text_similarity`).
        threshold: Similarity at/above which two values are considered dupes.
    """

    def __init__(
        self,
        profile_manager: Any = None,
        *,
        vector_store: Any = None,
        similarity=None,
        threshold: float = 0.82,
    ) -> None:
        self.profile_manager = profile_manager
        self.vector_store = vector_store
        self.similarity = similarity or text_similarity
        self.threshold = float(threshold)

    # -- public API -------------------------------------------------------
    def consolidate(self, *, mode: str = CONSOLIDATION_LIGHT) -> ConsolidationReport:
        """Run a consolidation pass. Never raises.

        ``light`` only merges obvious duplicates; ``deep`` additionally scans
        for contradictions.
        """
        report = ConsolidationReport(mode=mode)
        prof = self._profile()
        if prof is None:
            return report
        try:
            self._dedupe_list_fields(prof, report)
            if mode == CONSOLIDATION_DEEP:
                report.contradictions = self.detect_contradictions()
            if report.changed:
                self._save_profile()
        except Exception:  # pragma: no cover - housekeeping must not crash
            logger.debug("consolidation failed", exc_info=True)
        return report

    def detect_contradictions(self) -> List[Contradiction]:
        """Find conflicting stored beliefs. Never raises."""
        prof = self._profile()
        if prof is None:
            return []
        out: List[Contradiction] = []
        try:
            # Build a haystack of preference-like text with its source field.
            sources: List[tuple] = []
            for key, value in (getattr(prof, "preferences", {}) or {}).items():
                sources.append(("preferences", f"{key}: {value}".lower()))
            for item in getattr(prof, "interests", []) or []:
                sources.append(("interests", str(item).lower()))
            for a, b in _ANTONYM_GROUPS:
                has_a = next((s for s in sources if a in s[1]), None)
                has_b = next((s for s in sources if b in s[1]), None)
                if has_a and has_b:
                    out.append(
                        Contradiction(
                            field=has_a[0],
                            first=has_a[1],
                            second=has_b[1],
                            question=(
                                f"I've got conflicting notes — you seem to like "
                                f"both '{a}' and '{b}'. Which should I go with?"
                            ),
                        )
                    )
        except Exception:  # pragma: no cover - defensive
            logger.debug("detect_contradictions failed", exc_info=True)
        return out

    # -- internals --------------------------------------------------------
    def _dedupe_list_fields(self, prof: Any, report: ConsolidationReport) -> None:
        for fname in ("skills", "interests", "goals"):
            try:
                values = list(getattr(prof, fname, []) or [])
            except Exception:  # pragma: no cover - defensive
                continue
            if len(values) < 2:
                continue
            result = consolidate_texts(
                values, threshold=self.threshold, similarity=self.similarity
            )
            if result.removed > 0:
                setattr(prof, fname, list(result.items))
                report.duplicates_removed += result.removed
                report.fields_touched.append(fname)

    def _profile(self):
        pm = self.profile_manager
        if pm is None:
            return None
        return getattr(pm, "profile", None)

    def _save_profile(self) -> None:
        try:
            if self.profile_manager is not None:
                self.profile_manager.save()
        except Exception:  # pragma: no cover - best-effort
            logger.debug("could not save profile after consolidation", exc_info=True)
