# Changelog

All notable changes to MimOSA are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

For narrative, user-facing release notes see [`RELEASE_NOTES.md`](RELEASE_NOTES.md).

## [2.0.0-beta] — 2026-07-12 — Avatar Polish Sprint

Polish sprint on top of the v2.0.0-alpha avatar system: 12 UX and robustness
fixes covering setup, settings, defaults, performance, docs, and tests.

### Added

- **Setup wizard — "Your Avatar" step**: preset picker (feminine, masculine,
  neutral, or classic circle) with a paired default voice per preset.
- **Setup wizard — "Your Voice" step**: voice dropdown plus a ▶ Play Sample
  button to audition voices before choosing (threaded, degrades gracefully
  with no audio).
- **Settings — Avatar tab**: toggle avatar on/off, choose tier and voice,
  adjust animation speed, and audition the selected voice with Play Sample.
- **Avatar frame-rate governor** (`mimosa/avatar/performance.py`): opt-in
  auto-throttle that lowers FPS under sustained load and falls back to the
  classic circle if needed. No behavior change unless attached.
- **`docs/UPGRADING.md`**: v1.1.0 → v2.0.0 migration guide.
- **README** "What's New in v2.0" section.
- **Integration tests** (`tests/integration/`) for the avatar pipeline and
  setup-to-launch flow, plus frame-rate governor unit tests.

### Changed

- **New-install avatar defaults**: fresh installs now enable the avatar with a
  hardware-detected tier and a neutral character. Existing users upgrading from
  v1.x keep `avatar.enabled=false` (classic listening circle) for backward
  compatibility.
- Clearer setup wizard step titles ("Your Voice", "Your Avatar", "Wake Word").
- Clearer capability-detection fallback logging (reports the reason) and a
  startup log announcing the active visualization (avatar tier vs. circle).

### Fixed

- Suppressed repetitive audio-device error spam when no microphone/output is
  available; audio subsystems now degrade quietly and gracefully.
- Graceful headless degradation for UI dialogs (no GTK / no display).

## [2.0.0-alpha] — 2026-07-11 — The Avatar System

### Added

**Milestone 8.1: Avatar Infrastructure & Detection**
- **Avatar module** (`mimosa/avatar/`): New package with abstract `BaseAvatarRenderer` 
  and concrete `Sprite2DRenderer` for 2D character animation.
- **Hardware detection** (`mimosa/system/capability_detector.py`): Extended with 
  `detect_avatar_tier()` function supporting `none`, `basic_2d`, `advanced_2d`, and 
  `full_3d` capability tiers based on GPU/CPU detection.
- **Avatar configuration** (`mimosa/utils/config.py`): New `AvatarSettings` dataclass 
  with `enabled`, `tier`, `character_path`, `scale`, `position`, `transparency`, and 
  `fps` settings.
- **Avatar window** (`mimosa/ui/avatar_window.py`): New `AvatarCharacterWindow` 
  GTK4 transparent overlay for displaying animated avatars on desktop.

**Milestone 8.2: AI Avatar Generation Pipeline**
- **Avatar generator** (`mimosa/avatar/generator.py`): AI-powered avatar generation 
  with Stable Diffusion fallback and local model support.
- **Default avatars** (`data/avatars/`): Three hand-crafted SVG base characters 
  (feminine, masculine, neutral) for instant use without AI generation.
- **Sprite processor** (`mimosa/avatar/sprite_processor.py`): PIL/Pillow-based 
  pipeline for emotion variants, sprite sheet extraction, and format conversions.
- **Cache manager** (`mimosa/avatar/cache_manager.py`): Efficient avatar asset 
  caching with size limits and automatic cleanup.
- **Preview dialog** (`mimosa/avatar/preview_dialog.py`): Interactive avatar 
  selection and preview UI integrated into setup wizard.

**Milestone 8.3: Animation System & Lip Sync**
- **Emotion system** (`mimosa/avatar/emotions.py`): 6 emotion states (idle, listening, 
  thinking, speaking, happy, concerned) with visual parameters (color tint, scale, 
  brightness, pulse rate, rotation, blink rate).
- **Animator** (`mimosa/avatar/animator.py`): Unified state machine coordinating 
  emotions, lip sync, and gestures with smooth 300ms transitions.
- **Mouth shapes** (`mimosa/avatar/mouth_shapes.py`): 8 phoneme-mapped mouth shapes 
  (visemes) for realistic lip sync animation.
- **Lip sync engine** (`mimosa/avatar/lip_sync.py`): Real-time phoneme-driven mouth 
  animation synchronized with TTS playback.
- **Gesture system** (`mimosa/avatar/gestures.py`): 6 gesture animations (wave, 
  thinking, explaining, thumbs_up, shrug, point) with priority-based keyframe 
  interpolation.
- **Viseme mapper** (`mimosa/avatar/viseme_mapper.py`): TTS integration utilities 
  for speech duration estimation and phoneme timing.
- **Renderer integration** (`mimosa/avatar/renderer_2d.py`): Full animation support 
  including emotion-based color tinting, breathing/pulse, blinking, animated mouth 
  shapes, and gesture overlays.

**Milestone 8.4: Voice Selection & Integration Polish**
- **Voice library expansion** (`mimosa/avatar/voice_library.py`): 20 Piper voices 
  (8 feminine, 8 masculine, 4 neutral) with rich metadata (accent, pitch, style, 
  descriptions).
- **Voice auditioner** (`mimosa/avatar/voice_library.py`): Backend for voice preview 
  audio generation with caching.
- **Speech-avatar integration** (`mimosa/voice/voice_loop.py`): Speech callback 
  system with automatic duration estimation and thread-safe GTK marshaling.
- **Avatar speech wiring** (`mimosa/ui/app.py`): Full pipeline connecting voice loop 
  speech events → GTK main thread → renderer → animator → lip sync engine → animated 
  mouth shapes.
- **Comprehensive documentation** (`docs/AVATAR_SYSTEM.md`): 505-line guide covering 
  features, configuration, migration (v1.1.0 → v2.0.0-alpha), technical architecture, 
  performance benchmarks, troubleshooting, API reference, and contributing guidelines.

### Changed
- Version bumped to **2.0.0-alpha** across `mimosa/__init__.py`, `pyproject.toml`, 
  and `debian/changelog`.
- Default behavior: Existing users (v1.1.0): `avatar.enabled` defaults to `false` 
  (opt-in), preserving classic listening circle. New users: `avatar.enabled` defaults 
  to `true`.

### Deferred to v2.1.0
- Setup wizard voice+avatar selection UI (workaround: manual config editing)
- Settings UI avatar configuration tab (workaround: edit config file)
- Visual voice audition dialog (backend complete, UI missing)
- Rationale: Core functionality complete, UI polish can follow without blocking alpha

### Technical
- **New modules**: 13 avatar modules totaling ~5,400 lines of code
- **Test coverage**: 114 new tests (1,782 total, 13 skipped)
- **Performance**: 60 FPS on modern hardware, 30 FPS on low-end systems
- **Architecture**: Clean separation between emotion, animation, and rendering layers
- **Compatibility**: Avatar system is fully optional; all v1.1.0 features preserved

### Documentation
- `docs/AVATAR_SYSTEM.md`: Complete avatar system guide (MD, PDF, DOCX)
- `M8.3_COMPLETION_REPORT.md`: M8.3 animation system deliverables
- `M8.4_COMPLETION_REPORT.md`: M8.4 voice integration and full v2.0.0-alpha summary
- Migration guide for v1.1.0 → v2.0.0-alpha users

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
