# Phase 8 Handover — Production Readiness

**Phase 8 (Polish & Testing) is complete.** This is the final development
milestone; MimOSA is now a **production-ready release candidate** (`v1.0.0-rc.1`).
This handover is for whoever validates the RC and decides on promotion to a
final `1.0.0` on `main`.

---

## 1. State of the codebase

- **Branch:** `develop` (Phase 8 merged `--no-ff` from `milestone/m8.1`).
- **Tags:** `m8.1-complete` … `m8.4-complete`, `phase-8-complete`, and the
  release candidate `v1.0.0-rc.1`.
- **Tests:** `python -m pytest -q` → **1377 passed, 10 skipped** (offline,
  hermetic). The 10 skips are GTK-gated widget tests that need a display.
- **`main` is untouched** by design — promotion is a deliberate human step.

## 2. What "production-ready" means here

- The assistant **never crashes in the user's face**: all errors are converted
  to friendly spoken messages; tracebacks go to the log only.
- There is **one documented, rotating log** location.
- The optional Phase 7 services (tasks, resource monitoring, error-fix learning)
  are wired behind **settings toggles** and **off-by-default-safe**.
- Installation is **one command** and uninstallation cleanly removes (optionally
  purges) all data.
- Personalization works end-to-end (wizard → config → greeting).
- Docs (install, user, dev, troubleshooting, README) are current.

## 3. Recommended RC validation (needs a real desktop)

The automated suite is fully headless. Before cutting a final `1.0.0`, validate
on a real Kubuntu/Ubuntu desktop:

1. **GTK UI** — run the suite in a graphical session so the 10 skipped widget
   tests execute; manually open Settings (all 9 pages) and the setup wizard.
2. **Voice** — install `portaudio19-dev`, run `./install.sh --with-voice`, and
   confirm wake word → STT → reply → TTS with a real mic and speakers. Watch the
   first-run Whisper/Piper model download.
3. **Avatar/Companion** — install GTK4 packages, run `./install.sh --with-ui`,
   confirm the avatar animates and lip-syncs, the tray icon works, and the
   text-chat window shares the voice loop's brain.
4. **Cloud LLM (optional)** — set `ABACUS_API_KEY` and confirm richer answers,
   and that failures fall back locally without blocking.
5. **Lifecycle** — clean `./install.sh` → `mimosa --check` → `mimosa` →
   `./uninstall.sh --purge`, on a machine without an existing config.

See [`ISSUES_TO_ADDRESS.md`](ISSUES_TO_ADDRESS.md) for the full prerequisite
checklist.

## 4. Promotion to `main`

When the RC validation above passes:

```bash
git checkout main
git merge --no-ff develop -m "release: MimOSA 1.0.0"
git tag -a v1.0.0 -m "MimOSA 1.0.0"
# push main + tags
```

Do **not** auto-merge to `main` from automation — keep it a reviewed, manual
release step.

## 5. Future work (post-1.0 ideas)

- A real "check for updates" implementation on the About page.
- Packaging as a `.deb` / Flatpak for distro-native install.
- Optional cloud-sync of *non-sensitive* preferences (strictly opt-in).
- More skills and a richer custom-skill editor UI.

## 6. Where to look

| Topic | File |
|-------|------|
| Phase 8 summary | `PHASE_8_COMPLETION_REPORT.md` |
| Developer guide | `docs/POLISH_AND_TESTING.md` |
| Install guide | `INSTALL.md` |
| Troubleshooting | `docs/TROUBLESHOOTING.md` |
| User guide | `docs/USER_GUIDE.md` |
| Release notes | `RELEASE_NOTES.md` |
| Prerequisites | `ISSUES_TO_ADDRESS.md` |
