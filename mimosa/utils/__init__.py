"""Shared utilities package for MimOSA.

Cross-cutting helpers used throughout the codebase:

* **Structured logging** -- consistent, configurable logging separate from
  conversation logs (developer debugging only).
* **Configuration management** -- load and validate settings from
  ``config/`` and environment variables (``.env``).

Future modules expected here: ``logging.py`` and ``config.py``.
"""
