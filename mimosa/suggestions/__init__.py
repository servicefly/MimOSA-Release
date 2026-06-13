"""Context-aware proactive suggestions for MimOSA (M4).

This package lets MimOSA occasionally offer a genuinely helpful nudge -- "it's
9am, want me to open VS Code like usual?" -- based on the time of day and the
behavioural patterns it has learned.

* :class:`~mimosa.suggestions.proactive_suggester.ProactiveSuggester` turns the
  current context + learned patterns into candidate :class:`Suggestion` objects.
* :class:`~mimosa.suggestions.suggestion_engine.SuggestionEngine` adds the
  policy layer: confidence gating, "don't repeat yourself", an on/off setting,
  and success-rate tracking.

Everything is local-only, opt-out, and defensive (never raises into the UI).
"""

from __future__ import annotations

from mimosa.suggestions.proactive_suggester import ProactiveSuggester, Suggestion
from mimosa.suggestions.suggestion_engine import SuggestionEngine

__all__ = ["ProactiveSuggester", "Suggestion", "SuggestionEngine"]
