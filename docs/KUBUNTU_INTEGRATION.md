# Kubuntu 26.04 Integration (M2.3)

MimOSA is built and tuned for **Kubuntu 26.04** running **KDE Plasma**, but it
runs anywhere thanks to graceful degradation. M2.3 makes MimOSA *aware of the
machine it runs on* — the operating system, desktop session, and hardware — and
uses that awareness to adapt its behavior and answer questions about itself.

Everything here is **100 % local**: nothing about your hardware or OS is ever
sent to the cloud. The voice-facing `SystemInfoSkill` sets `uses_llm = False`,
and all detection is local file reads (`/etc/os-release`, `/proc`, `/sys`),
environment variables, `psutil`, and a handful of standard CLI probes.

---

## At a glance

| Module | Responsibility |
|--------|----------------|
| [`mimosa/system/system_profiler.py`](../mimosa/system/system_profiler.py) | OS / session profile: distro, version, desktop environment, display server, KDE Plasma version, architecture, kernel |
| [`mimosa/system/hardware_detector.py`](../mimosa/system/hardware_detector.py) | Hardware: audio backend, displays, microphones, CPU, RAM, GPU |
| [`mimosa/system/kde_integration.py`](../mimosa/system/kde_integration.py) | KDE Plasma features over D-Bus: notifications, virtual desktops, KDE Connect |
| [`mimosa/system/system_optimizer.py`](../mimosa/system/system_optimizer.py) | Hardware-aware tuning: audio backend, wake-word sensitivity, STT model, TTS quality, history limit |
| [`mimosa/skills/system_info.py`](../mimosa/skills/system_info.py) | `SystemInfoSkill` — answers spoken questions about the system |

---

## System profiling

`SystemProfiler` produces a cached, typed `SystemProfile`:

```python
from mimosa.system.system_profiler import SystemProfiler

profile = SystemProfiler().profile
print(profile.summary())
# -> "Ubuntu 26.04 LTS, KDE Plasma 6.0.4, Wayland, x86_64"
```

| Field | Source | Example |
|-------|--------|---------|
| `distro_id`, `distro_name`, `distro_version` | `/etc/os-release` (`ID`, `PRETTY_NAME`, `VERSION_ID`) | `ubuntu`, `Ubuntu 26.04 LTS`, `26.04` |
| `desktop_environment` | `XDG_CURRENT_DESKTOP` (→ `XDG_SESSION_DESKTOP` → `DESKTOP_SESSION`) | `KDE`, `GNOME` |
| `display_server` | `XDG_SESSION_TYPE` (inferred from `WAYLAND_DISPLAY`/`DISPLAY` if unset) | `wayland`, `x11` |
| `plasma_version` | `plasmashell --version`, else `KDE_SESSION_VERSION` | `6.0.4` |
| `architecture`, `kernel`, `kernel_version`, `hostname` | `platform.uname()` | `x86_64`, `6.11.0-generic` |
| `is_kubuntu`, `is_kde` | derived | `True` |

A missing `/etc/os-release`, an unset `XDG_CURRENT_DESKTOP`, or a non-KDE
session simply yields `None` for the affected field — never an exception. The
profile is detected once on first access and memoized; `refresh()` forces a
re-scan.

---

## Hardware detection

`HardwareDetector` produces a cached `HardwareProfile`:

```python
from mimosa.system.hardware_detector import HardwareDetector

hw = HardwareDetector().profile
print(hw.summary())
# -> "Intel Core i7-1260P (16 threads); 16 GB RAM; Intel graphics; PipeWire audio; 1 display"
```

| Area | How it's detected | Fallback |
|------|-------------------|----------|
| **CPU** | `psutil.cpu_count` / `cpu_freq`; model from `/proc/cpuinfo` | `os.cpu_count()` |
| **RAM** | `psutil.virtual_memory()` | parse `/proc/meminfo` |
| **GPU** | `lspci` (VGA/3D/Display controllers) | `/sys/class/drm/card*/device/vendor` PCI IDs |
| **Displays** | `xrandr --query` (resolution, primary, multi-monitor) | connected `/sys/class/drm/*/status` |
| **Audio** | `wpctl`/`pw-cli` → PipeWire; `pactl` → PulseAudio; `aplay`/`/proc/asound` → ALSA | none → `backend = None` |
| **Microphones** | `pactl list short sources` (excludes `.monitor`) | `arecord -l`, then `/proc/asound/cards` |

The detector distinguishes a *running* audio server from a merely *installed*
tool, and reports `multi_monitor` / `has_microphone` convenience flags.

---

## KDE integration

`KDEIntegration` wraps the KDE Plasma D-Bus services MimOSA needs. It chooses a
transport automatically:

1. **dbus-python** (`import dbus`) — the native, fast path.
2. **qdbus** subprocess (`qdbus6` / `qdbus-qt6` / `qdbus`) — CLI fallback.
3. **Neither** — every method returns `KDEResult(available=False, ...)` with a
   friendly spoken message instead of raising.

```python
from mimosa.system.kde_integration import KDEIntegration

kde = KDEIntegration(is_kde=True)
kde.send_notification("MimOSA", "Your timer is done.")   # KNotifications
kde.get_virtual_desktops()                               # KWin VirtualDesktopManager
kde.get_kde_connect_devices()                            # KDE Connect daemon
```

| Method | D-Bus service | Notes |
|--------|---------------|-------|
| `send_notification(title, body)` | `org.freedesktop.Notifications` | Works on any freedesktop desktop, not just KDE |
| `get_virtual_desktops()` | `org.kde.KWin` `/VirtualDesktopManager` | Reports the desktop count |
| `get_kde_connect_devices()` | `org.kde.kdeconnect` | Lists paired & reachable devices |
| `list_windows()` | `org.kde.KWin` | Honest about the lack of a stable cross-version window-list API |
| `capabilities()` | — | `{transport, available, is_kde_session}` |

On GNOME, a TTY session, or this project's CI box (no D-Bus), `available` is
`False` and the methods no-op gracefully.

---

## System optimization

`SystemOptimizer` reads the two profiles above and derives an `OptimizedConfig`
— pure logic, no I/O, so it's instant and deterministic.

```python
from mimosa.system.system_optimizer import SystemOptimizer

cfg = SystemOptimizer().config
# OptimizedConfig(audio_backend='pipewire', wake_word_sensitivity=0.5,
#                 whisper_model='small', tts_quality='high',
#                 max_history_turns=25, performance_tier='high', ...)
```

**Performance tiers** (from logical cores + total RAM):

| Tier | Condition | Whisper | TTS | Wake sensitivity |
|------|-----------|---------|-----|------------------|
| `high` | ≥ 8 cores **and** ≥ 16 GB | `small` | `high` | `0.5` |
| `low` | ≤ 2 cores **or** < 4 GB | `tiny` | `low` | `0.7` |
| `medium` | otherwise | `base` | `standard` | `0.6` |

Wake-word sensitivity is nudged up by `0.05` when no microphone is detected, to
compensate for unreliable input. The conversation-history limit scales with RAM
(5 turns at < 4 GB up to 40 turns at ≥ 32 GB) to keep memory bounded.

---

## Voice queries — `SystemInfoSkill`

The skill answers read-only questions locally (no LLM, no confirmation flow):

| You say | MimOSA answers with |
|---------|---------------------|
| "What desktop am I using?" | the desktop environment (+ Plasma version) |
| "Is this Wayland or X11?" | the display server |
| "What version of Plasma?" | the KDE Plasma version |
| "What operating system is this?" | the distro & whether it's Kubuntu |
| "Show me my system specs" | a combined OS + hardware summary |
| "What audio backend am I using?" | PipeWire / PulseAudio / ALSA + running state |
| "Do I have a microphone?" | detected capture devices |
| "How much RAM do I have?" / "What CPU?" / "What graphics card?" | the relevant hardware fact |
| "What settings do you recommend for this machine?" | the optimizer's tuning |

These route through **Tier-1 regex heuristics** in the intent router (intent
`system_info`) with **zero LLM calls**, and are matched *before* the
system-control patterns so "what audio backend am I using" is treated as a
question rather than a volume command.

---

## Hardware requirements

MimOSA's *minimum* footprint is modest because the optimizer scales the heavy
pieces (STT model, history) to the machine:

| Resource | Minimum | Recommended (Kubuntu 26.04 target) |
|----------|---------|------------------------------------|
| CPU | 2 cores | 8+ cores (for the `small` Whisper model) |
| RAM | 4 GB | 16 GB+ |
| Audio | ALSA | PipeWire (Kubuntu 26.04 default) |
| Microphone | 1 capture device | Built-in or USB mic |
| Desktop | any / headless | KDE Plasma 6 (for D-Bus features) |
| Display server | none | Wayland (Kubuntu 26.04 default) |

---

## Optional system dependencies

These unlock richer detection / features; MimOSA degrades cleanly without them:

| Tool / library | Enables |
|----------------|---------|
| `pciutils` (`lspci`) | precise GPU vendor/model |
| `x11-xserver-utils` (`xrandr`) | display resolutions & multi-monitor |
| `pipewire-utils` (`wpctl`, `pw-cli`) | PipeWire detection |
| `pulseaudio-utils` (`pactl`) | sink/source & microphone enumeration |
| `alsa-utils` (`aplay`, `arecord`) | ALSA fallback detection |
| `python3-dbus` **or** `qdbus` | KDE notifications, virtual desktops, KDE Connect |
| `plasma-workspace` (`plasmashell`) | precise KDE Plasma version |

---

## Health check

`scripts/health_check.py` now reports a full system profile and a **Kubuntu
26.04 / KDE compatibility** section: target-platform match, desktop/display
server, runtime audio & microphone verification, a KDE D-Bus capability report,
and the optimizer's recommended tuning. It emits warnings (never hard failures)
on non-Kubuntu hosts.

```bash
python scripts/health_check.py
```

---

## Design principles

* **Local & private** — no hardware/OS data leaves the machine; `uses_llm=False`.
* **Graceful degradation** — every probe may be missing; results are `None`/empty, never exceptions.
* **Bounded** — every subprocess call is timed out.
* **Cached** — detection runs once and is memoized; `refresh()` re-scans.
* **Testable** — every filesystem root, environment mapping, command runner,
  `psutil`, and D-Bus client is injectable, so the 117 M2.3 tests are fully
  hermetic and never depend on the host they run on.
