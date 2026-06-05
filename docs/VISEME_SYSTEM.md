# Viseme System — Enhanced TTS / Lip-Sync (M3.2)

This document describes MimOSA's **lip-sync** subsystem: how spoken audio is
turned into an animated mouth on the desktop avatar. It is the second milestone
of **Phase 3 — UI & Avatar**, building on the procedural avatar from M3.1.

> **Design principle — privacy first, degrade gracefully.** Every step runs
> **on-device**. Phonemes come from the *local* Piper/eSpeak engine that already
> synthesizes the speech; when phonemes aren't available we analyze the audio's
> own energy envelope. **No network calls, no third-party phoneme/viseme APIs,
> no telemetry.** If Piper, Cairo, or a display is missing, MimOSA keeps talking
> — it just shows the simpler M3.1 speaking animation. Viseme extraction is
> wrapped so a lip-sync fault can **never** crash the voice loop.

---

## 1. What is a viseme?

A *viseme* is the visual counterpart of a phoneme — the mouth shape made while
producing a sound. Many phonemes look identical on the lips (`p`, `b`, `m` are
all closed-lip "bilabials"), so a compact set of shapes is enough for
convincing lip-sync. MimOSA uses **10 shapes + silence**:

| Viseme | Example sounds | Mouth |
|--------|----------------|-------|
| `SILENCE` | pauses, end of speech | rest / closed neutral |
| `CLOSED` | p, b, m | lips pressed together |
| `LABIODENTAL` | f, v | lower lip to upper teeth |
| `DENTAL` | th | tongue between teeth, slight open |
| `ALVEOLAR` | t, d, n, l, s, z | tongue tip, slightly open |
| `VELAR` | k, g, ng | back of tongue, mid-open |
| `AFFRICATE` | ch, j, sh, zh | rounded / puckered |
| `WIDE` | ee, i, e | spread lips (smile) |
| `OPEN` | ah, aa | jaw dropped wide open |
| `ROUNDED` | oo, o, w | rounded, protruding lips |
| `MID` | uh, schwa, r | relaxed mid-open |

---

## 2. Pipeline overview

```
  text ──► PiperTTS.synthesize_with_visemes(text)
                │
                ├─► synthesize()  ───────────────► WAV bytes ─────────────┐
                │                                                          │
                └─► PhonemeExtractor.extract(...)                          │
                        │  (resolution order, first that works wins)       │
                        │   1. explicit Piper phoneme output (if given)    │
                        │   2. phonemes from injected phonemizer / voice   │
                        │   3. amplitude envelope of the WAV   (fallback)  │
                        │   4. empty timeline                  (last resort)│
                        ▼                                                   │
                 VisemeTimeline  ◄──────────────────────────────────────────┘
                        │   (frames of (viseme, start, end), + source tag)
                        ▼
        AvatarWindow.set_viseme_timeline(timeline)   [GTK main thread]
                        │
                        ▼
        AvatarRenderer ── AudioVisemeSync (playback clock)
                        │       │ current_window(t) → (current, next, blend)
                        ▼       ▼
                   MouthAnimator.update(dt)  → eases current MouthShape
                        │
                        ▼
                   MouthAnimator.draw(cr, …)  → Cairo bezier mouth
```

The voice worker thread produces the `(wav_bytes, timeline)` pair; the timeline
is handed to the GTK main thread which drives the animation off a monotonic
playback clock. The two threads share no mutable state — each call returns a
fresh WAV and timeline.

---

## 3. Modules

| Module | Layer | Responsibility | Heavy deps? |
|--------|-------|----------------|-------------|
| `mimosa/ui/viseme_mapper.py` | leaf (pure) | `Viseme` enum, IPA/ASCII phoneme→viseme table, `VisemeMapper`. | none |
| `mimosa/voice/phoneme_extractor.py` | voice | Timeline types (`PhonemeSpan`, `VisemeFrame`, `VisemeTimeline`), phoneme-timing estimation, Piper-output parsing, amplitude-envelope fallback, `PhonemeExtractor` orchestrator. | numpy *optional* |
| `mimosa/ui/audio_sync.py` | ui | `AudioVisemeSync` — maps a wall clock to a timeline position with latency compensation; pause/resume/resync. | none |
| `mimosa/ui/mouth_animator.py` | ui | `MouthShape`, per-viseme target shapes, frame-rate-independent easing, Cairo drawing. | pycairo *lazy* |
| `mimosa/voice/tts.py` | voice | `synthesize_with_visemes()` ties synthesis + extraction together. | piper *lazy* |
| `mimosa/ui/avatar_renderer.py` | ui | Drives the mouth during the `SPEAKING` state; falls back to the M3.1 bar. | pycairo *lazy* |

**Layering (no import cycles):** `viseme_mapper` (pure leaf) ← `phoneme_extractor`
(voice) ← `audio_sync` / `mouth_animator` / `avatar_renderer` (ui). The shared
timeline types live in `phoneme_extractor` so the UI imports *down* into voice,
never the reverse. `tts.py` imports the extractor **lazily / under
`TYPE_CHECKING`** so importing the voice package never pulls in the UI stack.

---

## 4. Phoneme → viseme mapping

`DEFAULT_PHONEME_TO_VISEME` maps both **eSpeak-ng IPA** symbols (what Piper
emits) and common **ASCII** fallbacks. Lookup in `phoneme_to_viseme()` is
robust and never raises, trying in order:

1. exact match,
2. match after stripping stress / length / combining diacritics
   (`ˈ ˌ ː ʰ` …, via Unicode NFD normalization),
3. first base character,
4. ASCII vowel heuristic,
5. "looks like a letter" → `ALVEOLAR`,
6. otherwise the neutral default (`MID`); `None` → `SILENCE`.

`VisemeMapper` wraps a table (a custom table is merged *over* the default), so
callers can override individual phonemes without re-specifying the whole map.

---

## 5. Timing — where the frame times come from

Piper's `--output-phonemes` emits phoneme **strings**, not per-phoneme
timestamps. Rather than depend on a forced-aligner (which would add a heavy,
often cloud-based dependency), MimOSA **estimates** timing locally:

* The known total audio duration is distributed across the phonemes weighted by
  viseme class — vowels/approximants are held longest (`1.7`), bilabial stops
  are briefest (`0.6`), fricatives/affricates sit in between. This yields
  natural pacing.
* If a Piper build *does* provide explicit `start`/`end`/`duration` per phoneme,
  `parse_piper_phoneme_output()` uses it directly (it accepts JSON objects,
  arrays, JSONL, and whitespace-separated phoneme strings).

Adjacent identical visemes are merged and sub-`MIN_FRAME_SECONDS` (30 ms)
frames are absorbed so the mouth doesn't flicker.

### Amplitude fallback

When no phonemes are available, `amplitude_to_viseme_timeline()` computes a
short-time RMS envelope (numpy-accelerated, with a pure-`struct` 16-bit
fallback) and maps energy bands to `SILENCE / ALVEOLAR / MID / OPEN`. The mouth
still opens and closes in time with the speech even without phoneme data.

---

## 6. Playback & animation

`AudioVisemeSync` anchors a monotonic clock when playback starts and reports
`position() = elapsed + latency_offset`. `latency_offset` (configurable,
clamped to `[-0.5, 1.0]`) compensates for audio-buffer lag so the mouth lines
up with what's heard. It supports `pause()`, `resume()`, and `resync(position)`
for drift correction against a real audio cursor.

`current_window(t)` returns `(current, next, blend)` — `blend` ramps up over the
trailing ~60 ms of each frame to give anticipatory **coarticulation**.

`MouthAnimator` keeps a *current* and *target* `MouthShape` (opening, width,
roundness, corner-lift, teeth). `update(dt)` eases current → target with
exponential smoothing `alpha = 1 - exp(-speed · dt)`, which is **frame-rate
independent** and never overshoots. `dt` is clamped (≤ 0.25 s) so a stalled
frame clock can't snap the mouth open. Three styles scale the result:
`natural` (default), `cartoon` (exaggerated), `minimal` (subtle).

`draw()` strokes the lips with Cairo bezier curves — a flat line when closed, a
filled lens with a dark interior, lip outline, and an optional teeth band when
open. `cairo` is imported lazily, so the module loads on headless machines and
`draw()` is simply a no-op when Cairo is missing.

---

## 7. Performance

* Per frame: a handful of float lerps for the shape plus a fixed, small number
  of bezier segments — constant work, comfortably under the 16 ms budget.
* No allocations in steady state beyond the Cairo path itself.
* numpy is used opportunistically for the amplitude envelope; the pure-Python
  path is only hit when numpy is absent.

---

## 8. Fallback chain (never crash, always degrade)

| Situation | Behavior |
|-----------|----------|
| Piper provides timed phonemes | Use exact timing. |
| Piper provides phoneme strings | Estimate timing from audio duration. |
| Voice can't phonemize | Amplitude-envelope visemes from the WAV. |
| Audio unreadable / empty | Empty timeline → M3.1 speaking bar. |
| Lip-sync disabled in config | M3.1 speaking bar. |
| No Cairo / no display | Headless; `draw()` is a no-op, voice unaffected. |
| Any exception during extraction | Caught; empty timeline; **audio still returned**. |
| Any exception during a frame tick | Caught; frame continues. |

---

## 9. Configuration (`UIConfig`)

| Field | Default | Meaning |
|-------|---------|---------|
| `lipsync_enabled` | `True` | Master toggle for the mouth animation. |
| `viseme_speed` | `14.0` | Mouth easing snappiness (clamped `1`–`40`). |
| `mouth_style` | `"natural"` | `natural` \| `cartoon` \| `minimal`. |
| `lipsync_latency` | `0.05` | Audio-buffer compensation, seconds (clamped `-0.5`–`1.0`). |
| `lipsync_debug` | `False` | Overlay live viseme/timing text for tuning. |

Preferences persist in the same local JSON file as the rest of the UI config —
no external storage.

---

## 10. Testing

Hermetic by design — no Piper, no audio device, no display required:

| Suite | Needs | Notes |
|-------|-------|-------|
| `test_viseme_mapper.py` | — | Enum, decoration stripping, lookup order, mapper merge. |
| `test_phoneme_extractor.py` | — | Timeline types, timing estimation, Piper-output parsing (strings/dict/timed/JSONL/malformed), in-memory WAV amplitude path, orchestration + fallbacks, never-raises. |
| `test_audio_sync.py` | — | Injected fake clock: position/latency/pause/resume/resync/finish. |
| `test_mouth_animator.py` | pycairo (draw tests skip if absent) | Shape clamp/lerp, easing toward targets, styles, speed clamp; draw paints pixels. |
| `test_lipsync_integration.py` | — | Renderer engage/tick/teardown, `from_config`, `UIConfig` roundtrip, `synthesize_with_visemes` phoneme/amplitude/bad-audio paths. |

```bash
# all lip-sync tests (always runnable, headless-safe):
python -m pytest tests/test_viseme_mapper.py tests/test_phoneme_extractor.py \
                 tests/test_audio_sync.py tests/test_mouth_animator.py \
                 tests/test_lipsync_integration.py
```

---

## 11. What's next (Phase 3)

* Wire `synthesize_with_visemes()` into the live voice loop / state bridge so
  spoken replies drive the mouth end-to-end (the renderer API is ready).
* Settings dialog controls for the lip-sync fields above.
* Sprite/expression layers on top of the procedural mouth.
