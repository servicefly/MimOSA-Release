# Upgrading MimOSA

This guide covers upgrading between MimOSA releases. Upgrades are designed to be
**safe and non-destructive**: your configuration, memory and preferences are
preserved, and new features stay opt-in until you choose them.

---

## Upgrading from v1.1.0 → v2.0.0

**v2.0.0 introduces the Avatar System.** The headline change is that MimOSA can
now display an animated character avatar (with a paired voice) instead of the
classic listening circle. The upgrade is fully backward-compatible.

### TL;DR

- ✅ **Your settings are kept.** The config is migrated in place; nothing is
  reset.
- ✅ **The avatar stays OFF for existing users.** You keep the classic listening
  circle until you explicitly pick an avatar. (Fresh installs get an avatar
  enabled by default.)
- ✅ **No new required setup.** MimOSA starts exactly as before.
- ✅ **Everything still degrades gracefully** — no GPU, no audio, or headless.

### How to upgrade

If you installed via the bootstrap script or a git clone:

```bash
cd MimOSA
git pull
./bootstrap.sh --yes    # refreshes the venv and dependencies
```

If you installed the package:

```bash
pipx upgrade mimosa      # or: pip install --upgrade mimosa
```

Then start MimOSA as usual. Your existing `settings.json` is detected and
migrated automatically on first launch.

### What changes in your config

v2.0.0 adds a new `avatar` section to the configuration:

```jsonc
"avatar": {
  "enabled": false,        // kept false for v1.x upgraders (opt-in)
  "tier": "circle_only",   // "2d" (animated sprite) or "circle_only"
  "custom_sprite_path": null,
  "voice_id": null          // paired TTS voice (null = Voice-page default)
}
```

- **Existing installs (upgrade):** `avatar.enabled` is set to `false`, so you
  continue to see the classic listening circle. Nothing about your voice,
  wake word, skills, privacy or memory changes.
- **New installs:** `avatar.enabled` is `true` and `tier` is auto-detected for
  your hardware (`2d` on a typical desktop, `circle_only` on very constrained
  machines).

### Turning on the avatar (optional)

Whenever you're ready to try the character avatar:

1. Open **Settings → Avatar**.
2. Toggle **Show character avatar** on.
3. Pick a **paired voice** and click **▶ Play Sample** to audition it.
4. (Optional) Adjust the **render tier** and **animation speed**.

You can switch back to the classic circle at any time by toggling the avatar
off — your other settings are untouched.

### Graceful fallback

The avatar automatically falls back to the classic listening circle when:

- the host hardware is too constrained (low RAM / few CPU cores), or
- GTK / a display is unavailable (headless), or
- you simply prefer the circle.

The active visualization is logged on startup, e.g.:

```
Visualization: character avatar (tier=2d)
Visualization: classic listening circle (avatar disabled)
```

### Rolling back

If you need to return to v1.1.0, your config remains compatible — the extra
`avatar` section is simply ignored by older versions. Reinstall the previous
release and start as usual.

---

## General upgrade tips

- **Back up your config** before major upgrades (optional but reassuring):
  `cp ~/.config/mimosa/settings.json ~/.config/mimosa/settings.json.bak`
- **Check the changelog** ([`CHANGELOG.md`](../CHANGELOG.md)) for the list of
  changes in each release.
- **Re-run the bootstrap script** after pulling to pick up new system/Python
  dependencies: `./bootstrap.sh --yes`.
