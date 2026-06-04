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

Future modules expected here: ``session.py``, ``long_term.py``,
``semantic.py``, ``private.py``, and ``file_index.py``.
"""
