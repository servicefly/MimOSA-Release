"""Shared utilities package for MimOSA.

Cross-cutting helpers used throughout the codebase:

* **Structured logging** -- consistent, configurable logging separate from
  conversation logs (developer debugging only).
* **Configuration management** -- load and validate settings from
  ``config/`` and environment variables (``.env``).

Modules
-------
* :mod:`mimosa.utils.config` (M3.3) -- the unified, versioned, thread-safe
  application configuration. Defines :class:`~mimosa.utils.config.AppConfig`
  (voice / skills / system-integration / privacy sections, embedding the UI
  preferences from :mod:`mimosa.ui.ui_config`) and
  :class:`~mimosa.utils.config.AppConfigManager` (atomic load/save, schema
  migration, observers, ``ui.json`` mirroring). Pure -- no GTK or heavy audio/ML
  imports -- so it loads and unit-tests on a headless machine. All settings are
  stored locally under ``~/.config/mimosa``; there is no telemetry.

Future modules expected here: ``logging.py``.
"""
