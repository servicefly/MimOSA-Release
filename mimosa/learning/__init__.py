"""Continuous learning subsystem for MimOSA (M4).

Where :mod:`mimosa.onboarding` handles the *one-time* "get to know you" chat,
this package lets MimOSA keep learning **organically during everyday use** --
quietly extracting facts from conversations, noticing behavioural patterns,
and (sparingly, and only with the user's blessing) asking a thoughtful
question now and then, just like a friend would.

Everything here is:

* **Local-only** -- nothing leaves the device.
* **Opt-out** -- governed by :class:`mimosa.utils.config.LearningSettings`.
* **Graceful** -- every public method is defensive and never raises into the
  conversation loop; a missing LLM falls back to heuristics, a missing vector
  store simply skips semantic storage.

Modules
-------
``pattern_detector``    -- detect tool/time/communication patterns from events.
``context_analyzer``    -- summarise the *current* moment (time of day, etc.).
``continuous_learner``  -- extract & store facts from ordinary conversations.
``proactive_questioner``-- decide *when* and *what* to ask, with rate limiting.
"""

from __future__ import annotations

from mimosa.learning.context_analyzer import ContextAnalyzer, ContextSnapshot
from mimosa.learning.continuous_learner import (
    ContinuousLearner,
    LearningOpportunity,
)
from mimosa.learning.pattern_detector import DetectedPattern, PatternDetector
from mimosa.learning.proactive_questioner import (
    ProactiveQuestion,
    ProactiveQuestioner,
)

__all__ = [
    "PatternDetector",
    "DetectedPattern",
    "ContextAnalyzer",
    "ContextSnapshot",
    "ContinuousLearner",
    "LearningOpportunity",
    "ProactiveQuestioner",
    "ProactiveQuestion",
]
