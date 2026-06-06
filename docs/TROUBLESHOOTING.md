# Troubleshooting MimOSA

MimOSA is designed to **never crash in your face** — every error is converted
into a calm, spoken-friendly message and a full traceback is written only to the
log file. This guide helps you resolve the most common situations.

> **First step for almost anything:** run `mimosa --check` (or
> `python scripts/health_check.py`) and look at the log file it points you to.

---

## Where are the logs?

| What | Location |
|------|----------|
| Main log (rotating) | `~/.local/share/mimosa/logs/mimosa.log` |
| Rotated backups | `mimosa.log.1` … `mimosa.log.3` in the same folder |

The log file rotates automatically at ~1 MiB and keeps the last 3 backups, so it
never grows without bound. Logs contain **no message content** by default —
only events and errors. Run `mimosa --check` to print the exact path on your
machine, or launch with `--verbose` for more detail (or `--no-log-file` to log
to the console only).

---

## Installation issues

### `python3: command not found` or version too old
MimOSA needs **Python 3.10+**. Install a newer Python (e.g.
`sudo apt install python3.11 python3.11-venv`) and re-run `./install.sh`.

### `install.sh` fails creating the virtual environment
Make sure the `venv` module is available:
```bash
sudo apt install python3-venv
```

### `pip install` is very slow or downloads a huge package
The **voice** extra depends on `openai-whisper`, which pulls in **PyTorch**
(hundreds of MB). This is expected. If you don't need voice yet, install the
core only (`./install.sh`) and add `--with-voice` later.

---

## Voice / microphone issues

### "No microphone found" or audio capture fails
- Install the PortAudio system library: `sudo apt install portaudio19-dev`,
  then reinstall the voice extra: `./install.sh --with-voice`.
- Confirm your user can access audio devices (e.g. is in the `audio` group).
- MimOSA degrades gracefully: with no working mic it stays in a text/headless
  mode instead of crashing.

### Wake word never triggers
- A Picovoice **Porcupine** key (`PORCUPINE_ACCESS_KEY`) enables the precise
  wake word. Without it, MimOSA uses a simpler **energy-based** fallback that
  may need a louder/closer trigger.

### First voice interaction hangs for a while
- The first run downloads the **Whisper** and **Piper** models. This is a
  one-time download; subsequent runs are fast.

---

## UI / avatar issues

### The desktop avatar doesn't appear
- The avatar needs GTK4: `sudo apt install libgtk-4-1 gir1.2-gtk-4.0`, then
  `./install.sh --with-ui`.
- On a headless machine or over SSH without a display, MimOSA automatically runs
  in headless mode — this is expected, not an error.

### Settings window controls are hard to use with a keyboard / screen reader
- All settings pages and the setup wizard ship with keyboard navigation,
  screen-reader labels, and high-contrast-friendly widgets. If something seems
  unlabeled, please file an issue with the page name.

---

## Language-model (LLM) issues

### "I can answer that better with my cloud brain" / generic answers
- For richer answers, set `ABACUS_API_KEY` in `.env`. Without a key (or with
  `USE_LOCAL_LLM=true`), MimOSA uses local fallbacks — answers are simpler but
  fully private.

### Cloud requests fail / time out
- Check your internet connection and that `ABACUS_API_KEY` is valid.
- MimOSA never blocks on a failed cloud call — it falls back locally and tells
  you what happened.

---

## Data, privacy & maintenance

### How do I clear my history?
- Conversation history lives in `~/.local/share/mimosa/`. You can set a
  **data-retention period** in **Settings → Privacy & Data**; MimOSA purges
  messages older than that and reclaims disk space automatically on startup.
- To wipe everything, run `./uninstall.sh --purge`.

### Background tasks are using too much CPU
- Open **Settings → Background Tasks** and disable background tasks, or lower
  the concurrency / CPU & memory thresholds. The **resource monitor** already
  pauses background work when your system is busy.

---

## Still stuck?

1. Run `mimosa --check` and copy the output.
2. Open the log file at `~/.local/share/mimosa/logs/mimosa.log` and look at the
   last few lines (the friendly message you heard maps to a detailed entry
   there).
3. File an issue at <https://github.com/servicefly/MimOSA/issues> with the
   `--check` output and the relevant log lines (they contain no private message
   content).
