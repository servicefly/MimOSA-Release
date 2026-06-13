# Changelog

All notable changes to MimOSA are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

For narrative, user-facing release notes see [`RELEASE_NOTES.md`](RELEASE_NOTES.md).

## [1.1.0] — 2026-06-13 — Continuous Learning & Polish

### Added
- **Continuous learning** (`mimosa/learning/continuous_learner.py`): facts and
  preferences are now extracted from everyday conversations (not just onboarding)
  and folded into the local profile. Wired into the voice loop (best-effort).
- **Pattern detection** (`mimosa/learning/pattern_detector.py`): tracks tool,
  timing and communication-style habits.
- **Context analysis** (`mimosa/learning/context_analyzer.py`): time-of-day and
  recent-activity awareness used to drive suggestions.
- **Proactive questions** (`mimosa/learning/proactive_questioner.py`): occasional,
  non-repeating "get to know you" questions, rate-limited to 1–2/day with
  `rarely` / `balanced` / `often` frequency.
- **Memory consolidation** (`mimosa/memory/consolidator.py`): merges duplicates,
  reconciles contradictions (newer wins) and prunes clutter, with light and deep
  passes.
- **Relationship tracking** (`mimosa/memory/relationship_tracker.py`): models a
  new → familiar → close progression that adjusts the assistant's tone.
- **Context-aware suggestions** (`mimosa/suggestions/`): high-confidence (>70%)
  nudges based on detected patterns and current context.
- **Memory viewer UI** (`mimosa/ui/memory_viewer.py`): view profile, patterns,
  relationship and asked-questions.
- **Settings → Learning** page: Learning Preferences (allow questions, frequency,
  proactive suggestions, learn-from-conversations), Memory Management (view
  memory, consolidate now) and a relationship-stage readout.
- **Conversation intelligence**: light emotion detection and reference resolution
  in `mimosa/core/conversation_manager.py`.
- **`LearningSettings`** dataclass in `mimosa/utils/config.py`.
- New docs: `docs/CONTINUOUS_LEARNING.md`, `docs/PRIVACY.md`; updates to
  `docs/USER_GUIDE.md`, `docs/MEMORY_SYSTEM.md` and `README.md`.

### Changed
- `mimosa/llm/persona.py`: `build_system_prompt` accepts a `relationship_note`
  to gently shape tone by relationship stage.
- Version bumped to **1.1.0** across `mimosa/__init__.py`, `pyproject.toml`,
  packaging docs and `debian/changelog`.

### Preserved / Compatibility
- All Milestone 1–3 features and tests are preserved.
- Backward compatible: existing v1.0.0 profiles and config load unchanged
  (new `learning` settings are absent-safe with sensible defaults).
- All new behaviour is local-only and opt-out; learning never gates core
  functionality and never interrupts a conversation.

## [1.0.0] — 2026-06 — First Full Release

- Milestone 3: conversational onboarding & on-device memory system.
- See [`RELEASE_NOTES.md`](RELEASE_NOTES.md) for full details and earlier
  release-candidate history (Milestones 1–2).
