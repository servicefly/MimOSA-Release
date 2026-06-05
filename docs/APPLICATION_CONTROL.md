# Application Launching & System Control (M2.2)

MimOSA's second wave of **system integration** skills. They let the user launch,
list, query, and close desktop applications, and adjust low-level system state —
volume, screen brightness, Wi-Fi, and battery — entirely by voice, with **zero
cloud calls**.

This document complements:

- [`VOICE_PIPELINE.md`](VOICE_PIPELINE.md) — how speech becomes text.
- [`INTENT_SYSTEM.md`](INTENT_SYSTEM.md) — how text is classified and routed.
- [`FILE_OPERATIONS.md`](FILE_OPERATIONS.md) — the M2.1 file skill (same patterns).

---

## 1. Where it sits in the pipeline

```
 mic ─▶ STT ─▶ IntentRouter ─┬─▶ ApplicationSkill ──▶ AppRegistry (.desktop scan)
                             │                    └─▶ psutil (launch / find / kill)
                             │
                             └─▶ SystemControlSkill ─▶ SystemController
                                                          ├─ volume   (wpctl/pactl/amixer)
                                                          ├─ brightness (brightnessctl/xbacklight)
                                                          ├─ wifi      (nmcli)
                                                          └─ battery   (/sys/class/power_supply)
```

The router classifies these commands locally (regex Tier 1, **zero LLM calls**)
and dispatches to the right skill.

**Privacy:** every operation is 100 % on-device. Both skills set
`uses_llm = False`; nothing about your apps or system is ever sent to the cloud.

---

## 2. Components

| File | Responsibility |
|------|----------------|
| `mimosa/system/app_registry.py` | Discover installed apps by parsing `.desktop` files; fuzzy name lookup |
| `mimosa/skills/application.py` | `ApplicationSkill` — launch / list / status / close apps |
| `mimosa/system/system_control.py` | `SystemController` — backend-agnostic volume/brightness/Wi-Fi/battery |
| `mimosa/skills/system_control.py` | `SystemControlSkill` — NL parsing for system commands |

All side-effecting backends (process spawn/kill, subprocess execution, tool
discovery) are **injectable** constructor arguments, which keeps the test-suite
fully hermetic — no real apps are launched and no system state is touched.

---

## 3. Application control

### 3.1 The app registry

`AppRegistry` builds a searchable catalog by parsing freedesktop `.desktop`
entries from the standard locations:

- `~/.local/share/applications` (per-user, highest precedence)
- `/usr/share/applications`, `/usr/local/share/applications`
- Flatpak / Snap export dirs, plus anything in `$XDG_DATA_DIRS`

Each entry yields an `AppEntry` with: `app_id`, `name`, `exec_command` (with
`%u`/`%f`/… field codes stripped), `icon`, `categories`, `comment`, `terminal`,
and `keywords`.

**Robust parsing.** A real applications directory is messy. The parser:

- skips files with no `[Desktop Entry]` section, missing `Name`/`Exec`, or a
  non-`Application` `Type`;
- honours `NoDisplay=true` / `Hidden=true` (never surfaced);
- tolerates malformed/duplicate keys and bad encodings without crashing — a bad
  file is logged at debug level and skipped.

**Lazy + cached.** The catalog is built on first use and cached. Call
`registry.refresh()` to rebuild after installing/removing an app.

**Fuzzy matching.** `find(query)` blends exact, substring, token-overlap, and
`difflib` ratio scoring so imperfect speech-to-text ("fire fox" → Firefox,
"editor" → KWrite via its keyword) still resolves. `rank(query)` exposes scored
candidates for "did you mean …?" suggestions.

#### Test override

Set `MIMOSA_APP_DIRS` (an `os.pathsep`-separated list) to point the registry at
a temp directory of fixture `.desktop` files — mirroring M2.1's
`MIMOSA_FILE_ROOT`. This keeps the unit tests hermetic.

### 3.2 The skill

| You say | What happens |
|---------|--------------|
| "open Firefox", "launch the text editor", "start Dolphin" | Resolve via registry → spawn detached, with a startup check |
| "what browsers do I have?", "list my office apps" | List apps by freedesktop category |
| "is Firefox running?" | Look up live processes via `psutil` |
| "close Firefox", "quit the music player" | **Confirm**, then SIGTERM → SIGKILL fallback |

**Safety:**

- An app is validated against the registry **before** launch; unknown names get
  a "did you mean …?" suggestion instead of a launch.
- Launches are spawned fully **detached** (`start_new_session=True`, no inherited
  std streams) with a brief startup check so a failing binary can't hang the
  voice loop.
- **Closing an app is state-changing**, so it is a two-step confirmation
  (mirroring the M2.1 delete flow): the skill describes the action and waits for
  "yes"/"no" on the next turn. Termination is graceful (`SIGTERM`, then `SIGKILL`
  after a timeout, or immediately when "force"/"kill" is said).

---

## 4. System control

`SystemController` wraps the relevant CLI tools behind one uniform, defensive
API. Every method returns a `CommandResult(success, message, data)`.

| Domain | Backends (first available wins) | Commands |
|--------|---------------------------------|----------|
| Volume | `wpctl` → `pactl` → `amixer` | up / down / set % / mute / unmute / query |
| Brightness | `brightnessctl` → `xbacklight` | up / down / set % / query |
| Wi-Fi | `nmcli` | on / off (confirmed) / status |
| Battery | `/sys/class/power_supply/BAT*` (no tool needed) | charge % + charging state |

**Graceful degradation.** Each operation probes for an available backend with
`shutil.which`. If none is installed, it returns a clean *"I can't control the
volume because no supported tool (wpctl or pactl or amixer) is installed"* —
never a crash. (This is exactly the situation on a headless CI box, which is why
the tests inject a fake shell.)

**Bounded.** Every subprocess call runs with a timeout (default 5 s) so a hung
helper can never freeze the assistant.

### The skill

| You say | What happens |
|---------|--------------|
| "turn the volume up", "louder", "volume down 20" | Relative change (default 10 %) |
| "set volume to 30 percent" | Absolute set (clamped 0–100) |
| "mute" / "unmute" | Toggle output mute |
| "brightness up", "dim the screen", "set brightness to 70%" | Relative / absolute brightness |
| "turn wifi off" | **Confirm** (it disconnects you), then `nmcli radio wifi off` |
| "turn wifi on", "is my wifi on?" | Enable / status report |
| "how much battery do I have left?" | Read charge % + charging state from sysfs |

Reversible, low-impact changes (volume/brightness) act immediately. Turning
Wi-Fi **off** is confirmed first because it can drop the user's connection.

---

## 5. Routing

`IntentRouter` adds two Tier-1 intents — `application` and `system_control` —
matched by regex with **zero LLM calls**. Ordering matters:

```
time → calculator → weather → file_ops → system_control → application → greeting
```

- **File before app/system**, so "open my notes **file**" stays a file op while
  "open Firefox" is an app launch.
- **System before app**, so "turn the **volume** up" isn't read as launching an
  app.

Both skills implement `has_pending_confirmation()`, so a bare "yes"/"no" after a
close/Wi-Fi-off prompt is routed straight back to the waiting skill instead of
being re-classified.

---

## 6. System dependencies

These are **optional** — MimOSA degrades gracefully when they're absent — but
recommended on Kubuntu 26.04 for full functionality:

```bash
# Audio (PipeWire is default on modern Kubuntu; wpctl ships with it)
sudo apt install wireplumber        # provides wpctl
#   …or PulseAudio utils / ALSA:
sudo apt install pulseaudio-utils alsa-utils

# Brightness
sudo apt install brightnessctl

# Wi-Fi (NetworkManager is the Kubuntu default)
sudo apt install network-manager    # provides nmcli

# App launching uses the freedesktop .desktop catalog already present on any
# desktop install; process control uses psutil (see requirements.txt).
```

Battery readings need no extra package — they come straight from
`/sys/class/power_supply`.

---

## 7. Testing

```bash
pytest -q tests/test_application.py      # registry + ApplicationSkill (41 tests)
pytest -q tests/test_system_control.py   # SystemController + skill (42 tests)
```

Both suites are fully offline and hermetic:

- `MIMOSA_APP_DIRS` redirects the registry at a temp dir of fixture `.desktop`
  files;
- process spawn / list / terminate are injected fakes (no real apps touched);
- the `SystemController`'s subprocess `runner` and `which` are injected fakes,
  and the battery sysfs root is a `tmp_path` — so the tests pass even on a box
  with none of the real tools installed.
```
