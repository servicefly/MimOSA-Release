"""Memory subsystem package for MimOSA.

Implements the multi-tier memory architecture that lets MimOSA remember
context, learn preferences, and protect private conversations:

* **Session memory** -- the current conversation (lightweight JSON).
* **Long-term memory** -- user preferences and history (SQLite).
* **Semantic memory** -- vector embeddings for relevant-context retrieval
  (Chroma).
* **Private memory** -- encrypted storage (SQLCipher) for sensitive
  conversations that must *never* be sent to any cloud service.
* **File index** -- a human-readable, LLM-parseable Markdown map of the
  user's filesystem.

Modules (M5 — Memory & Context)
-------------------------------
* :mod:`mimosa.memory.conversation_store` (M5.1) — :class:`ConversationStore`,
  a SQLite-backed durable conversation history wired into
  :class:`mimosa.core.conversation_manager.ConversationManager`.
* :mod:`mimosa.memory.preference_learner` (M5.2) — :class:`PreferenceLearner`,
  silent background learning of user patterns with confidence scoring.
* :mod:`mimosa.memory.semantic_memory` (M5.3) — :class:`SemanticMemory`,
  on-device embeddings (Chroma + sentence-transformers) with a pure-Python
  fallback so it degrades gracefully when those optional deps are absent.
* :mod:`mimosa.memory.privacy_guard` (M5.4) — :class:`PrivacyGuard`, a hybrid
  (keyword → user-pattern → optional local-LLM) detector that routes sensitive
  queries to local-only models and keeps them out of cloud context.
* :mod:`mimosa.memory.paths` — on-device data-directory resolution
  (``MIMOSA_DATA`` / ``XDG_DATA_HOME``).
"""

from mimosa.memory.conversation_store import (
    ConversationStore,
    StoredMessage,
    StoredSession,
)
from mimosa.memory.paths import (
    conversations_db_path,
    default_data_dir,
    preferences_db_path,
    private_db_path,
    semantic_store_dir,
)
from mimosa.memory.preference_learner import (
    LearnedPreference,
    PreferenceLearner,
)
from mimosa.memory.privacy_guard import (
    PrivacyAssessment,
    PrivacyGuard,
    Sensitivity,
)
from mimosa.memory.semantic_memory import (
    HAS_CHROMA,
    SemanticMemory,
    SemanticResult,
)

__all__ = [
    "ConversationStore",
    "StoredMessage",
    "StoredSession",
    "PreferenceLearner",
    "LearnedPreference",
    "SemanticMemory",
    "SemanticResult",
    "HAS_CHROMA",
    "PrivacyGuard",
    "PrivacyAssessment",
    "Sensitivity",
    "default_data_dir",
    "conversations_db_path",
    "preferences_db_path",
    "private_db_path",
    "semantic_store_dir",
]
