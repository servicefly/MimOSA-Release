"""Application launching & control skill (M2.2).

This skill lets the user manage desktop applications by voice:

* **Launch** -- "open Firefox", "launch the text editor", "start Dolphin".
* **List** -- "what browsers do I have?", "list my office apps".
* **Check** -- "is Firefox running?".
* **Close** -- "close Firefox", "quit the music player".

It combines two local, private building blocks:

* :class:`~mimosa.system.app_registry.AppRegistry` -- discovers installed apps
  by parsing ``.desktop`` files and fuzzily resolves a spoken name to a real
  application (handling imperfect speech-to-text).
* :mod:`psutil` -- inspects and terminates running processes so the skill can
  tell whether an app is running and close it gracefully (SIGTERM first, then
  SIGKILL as a fallback).

Design principles
-----------------
* **Local & private.** ``uses_llm`` is ``False``; no text leaves the device.
* **Safe.** An app is validated against the registry *before* launch, launches
  are spawned detached with a startup timeout so a failing binary can't hang
  the voice loop, and *closing* an app is treated as a state-changing action
  that asks for confirmation first (mirroring the file-ops safety pattern).
* **Testable.** Process spawning (``spawn``), process discovery
  (``process_lister``), and the registry are all injectable, so the unit tests
  run with zero real applications launched.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from mimosa.skills.base_skill import BaseSkill, SkillResult
from mimosa.system.app_registry import AppEntry, AppRegistry

try:  # psutil is the standard cross-distro process library.
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional at import time
    psutil = None  # type: ignore


# Words that confirm / cancel a queued action on the following turn.
_CONFIRM_WORDS = re.compile(
    r"^\s*(yes|yep|yeah|yup|confirm|confirmed|do it|go ahead|proceed|sure|ok|okay|"
    r"affirmative|please do)\b",
    re.IGNORECASE,
)
_CANCEL_WORDS = re.compile(
    r"^\s*(no|nope|cancel|stop|don'?t|never ?mind|abort|forget it)\b",
    re.IGNORECASE,
)

#: How long to wait (seconds) for a freshly-spawned process to stay alive
#: before we report a launch as successful.
LAUNCH_CONFIRM_DELAY = 0.3
#: How long to wait for a terminated process to exit before escalating to kill.
TERMINATE_TIMEOUT = 3.0


@dataclass
class _PendingAction:
    """A state-changing action (e.g. closing an app) awaiting confirmation."""

    description: str
    execute: Callable[[], SkillResult]
    created: float = field(default_factory=time.time)


def _default_spawn(argv: List[str]) -> int:
    """Launch ``argv`` detached from MimOSA and return the child PID.

    The process is fully detached (new session, no inherited std streams) so the
    launched GUI app outlives the assistant and never blocks it. Separated out
    so tests can inject a fake spawner.
    """
    proc = subprocess.Popen(  # noqa: S603 - argv from trusted .desktop Exec
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    return proc.pid


def _default_process_lister() -> List[dict]:
    """Return a list of ``{pid, name, exe, cmdline}`` dicts for live processes."""
    if psutil is None:  # pragma: no cover - psutil normally present
        return []
    procs = []
    for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
        try:
            info = p.info
            procs.append(
                {
                    "pid": info.get("pid"),
                    "name": (info.get("name") or "").lower(),
                    "exe": (info.get("exe") or "").lower(),
                    "cmdline": " ".join(info.get("cmdline") or []).lower(),
                }
            )
        except Exception:  # noqa: BLE001 - process vanished / access denied
            continue
    return procs


class ApplicationSkill(BaseSkill):
    """Launch, list, query and close desktop applications by voice."""

    name = "application"
    intents = ["application", "app_launch"]
    uses_llm = False

    def __init__(
        self,
        llm_provider=None,
        *,
        registry: Optional[AppRegistry] = None,
        spawn: Optional[Callable[[List[str]], int]] = None,
        process_lister: Optional[Callable[[], List[dict]]] = None,
        terminator: Optional[Callable[[int, bool], bool]] = None,
    ) -> None:
        """Construct the skill.

        Args:
            llm_provider: Unused (kept for a uniform constructor); local skill.
            registry: An :class:`AppRegistry`. Defaults to a fresh one scanning
                the standard locations (honours ``MIMOSA_APP_DIRS``).
            spawn: Callable launching an argv list, returning a PID. Injectable
                for tests; defaults to :func:`_default_spawn`.
            process_lister: Callable returning live-process dicts. Injectable;
                defaults to a :mod:`psutil`-backed lister.
            terminator: Callable ``(pid, force) -> bool`` to terminate a PID.
                Injectable; defaults to a :mod:`psutil`/``os.kill`` terminator.
        """
        super().__init__(llm_provider=llm_provider)
        self.registry = registry or AppRegistry()
        self._spawn = spawn or _default_spawn
        self._list_processes = process_lister or _default_process_lister
        self._terminate = terminator or self._default_terminator
        self._pending: Optional[_PendingAction] = None

    # ------------------------------------------------------------------
    # Natural-language entry point
    # ------------------------------------------------------------------

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        utterance = (text or "").strip()
        lowered = utterance.lower()

        if self._pending is not None:
            return self._resolve_pending(lowered)

        if not lowered:
            return self._fail(
                "I didn't catch an app command. Try 'open Firefox' or "
                "'is the music player running?'."
            )

        # Order matters: detect "close/quit" and "is X running" before launch
        # so "close firefox" isn't read as "open ... firefox".
        if re.search(r"\b(close|quit|kill|exit|terminate|stop|end)\b", lowered):
            return self._parse_close(utterance, lowered)
        if re.search(r"\b(is|are)\b.*\b(running|open|active)\b", lowered) or re.search(
            r"\brunning\b", lowered
        ):
            return self._parse_is_running(utterance, lowered)
        if re.search(r"\b(list|show|what|which)\b", lowered) and re.search(
            r"\b(apps?|applications?|programs?|browsers?|editors?|games?|tools?)\b", lowered
        ):
            return self._parse_list(utterance, lowered)
        if re.search(r"\b(open|launch|start|run|fire up|bring up)\b", lowered):
            return self._parse_launch(utterance, lowered)

        # Bare app name fallback ("Firefox") -> try to launch it.
        return self._parse_launch(utterance, lowered)

    # ------------------------------------------------------------------
    # Confirmation handling (mirrors FileOperationsSkill)
    # ------------------------------------------------------------------

    def _resolve_pending(self, lowered: str) -> SkillResult:
        pending = self._pending
        if _CONFIRM_WORDS.match(lowered):
            self._pending = None
            return pending.execute()
        if _CANCEL_WORDS.match(lowered):
            self._pending = None
            return SkillResult(
                text="Okay, I'll leave it as is.",
                skill=self.name,
                metadata={"operation": "cancel"},
            )
        return SkillResult(
            text=(
                f"I still need a yes or no. {pending.description} "
                "Say 'yes' to proceed or 'no' to cancel."
            ),
            success=False,
            skill=self.name,
            metadata={"operation": "awaiting_confirmation"},
        )

    def _queue(self, description: str, execute: Callable[[], SkillResult]) -> SkillResult:
        self._pending = _PendingAction(description=description, execute=execute)
        return SkillResult(
            text=f"{description} Say 'yes' to confirm or 'no' to cancel.",
            success=True,
            skill=self.name,
            metadata={"operation": "confirm_required", "pending": description},
        )

    def has_pending_confirmation(self) -> bool:
        return self._pending is not None

    # ------------------------------------------------------------------
    # Programmatic operations
    # ------------------------------------------------------------------

    def launch_app(self, app: AppEntry) -> SkillResult:
        """Launch a resolved :class:`AppEntry`, detached, with a startup check."""
        argv = app.argv()
        if not argv:
            return self._fail(f"I couldn't work out how to launch {app.name}.",
                              operation="launch")
        try:
            pid = self._spawn(argv)
        except FileNotFoundError:
            return self._fail(
                f"I couldn't launch {app.name} because its program wasn't found.",
                operation="launch",
            )
        except OSError as exc:
            return self._fail(f"I couldn't launch {app.name}: {exc}.", operation="launch")

        # Give it a brief moment; if it immediately died, report failure.
        time.sleep(LAUNCH_CONFIRM_DELAY)
        if pid and not self._pid_alive(pid):
            # Some launchers fork and exit (the real app re-parents); only warn
            # if we can positively confirm nothing related is running.
            if not self._find_running(app):
                return self._fail(
                    f"I tried to open {app.name}, but it closed immediately. "
                    "It may have failed to start.",
                    operation="launch",
                    extra={"pid": pid},
                )
        return SkillResult(
            text=f"Opening {app.name}.",
            skill=self.name,
            metadata={"operation": "launch", "app": app.name, "app_id": app.app_id, "pid": pid},
        )

    def is_running(self, app: AppEntry) -> bool:
        """Return ``True`` if a process matching ``app`` appears to be running."""
        return bool(self._find_running(app))

    def close_app(self, app: AppEntry, *, force: bool = False) -> SkillResult:
        """Terminate the process(es) backing ``app`` (SIGTERM, then SIGKILL)."""
        pids = self._find_running(app)
        if not pids:
            return self._fail(f"{app.name} doesn't seem to be running.", operation="close")

        closed = []
        for pid in pids:
            try:
                if self._terminate(pid, force):
                    closed.append(pid)
            except Exception:  # noqa: BLE001 - process may have already exited
                continue
        if not closed:
            return self._fail(f"I couldn't close {app.name}.", operation="close")
        return SkillResult(
            text=f"Closed {app.name}.",
            skill=self.name,
            metadata={"operation": "close", "app": app.name, "pids": closed},
        )

    # ------------------------------------------------------------------
    # NL parsers
    # ------------------------------------------------------------------

    def _parse_launch(self, utterance: str, lowered: str) -> SkillResult:
        target = self._extract_app_name(
            utterance, lowered, ("open", "launch", "start", "run", "fire up", "bring up")
        )
        if not target:
            return self._fail("Which application should I open?", operation="launch")
        app = self.registry.find(target)
        if app is None:
            suggestion = self._suggestion(target)
            return self._fail(
                f"I couldn't find an app called '{target}'.{suggestion}",
                operation="launch",
                extra={"query": target},
            )
        return self.launch_app(app)

    def _parse_close(self, utterance: str, lowered: str) -> SkillResult:
        force = bool(re.search(r"\b(force|kill|forcefully)\b", lowered))
        target = self._extract_app_name(
            utterance, lowered,
            ("close", "quit", "kill", "exit", "terminate", "stop", "end", "force"),
        )
        if not target:
            return self._fail("Which application should I close?", operation="close")
        app = self.registry.find(target)
        if app is None:
            return self._fail(
                f"I couldn't find an app called '{target}'.",
                operation="close", extra={"query": target},
            )
        if not self.is_running(app):
            return self._fail(f"{app.name} doesn't seem to be running.", operation="close")
        how = "force close" if force else "close"
        return self._queue(
            f"I'm about to {how} {app.name}.",
            lambda: self.close_app(app, force=force),
        )

    def _parse_is_running(self, utterance: str, lowered: str) -> SkillResult:
        target = self._extract_app_name(
            utterance, lowered, ("is", "are", "running", "open", "active")
        )
        if not target:
            return self._fail("Which application do you mean?", operation="status")
        app = self.registry.find(target)
        if app is None:
            return self._fail(
                f"I couldn't find an app called '{target}'.",
                operation="status", extra={"query": target},
            )
        running = self.is_running(app)
        msg = f"Yes, {app.name} is running." if running else f"No, {app.name} isn't running."
        return SkillResult(
            text=msg,
            skill=self.name,
            metadata={"operation": "status", "app": app.name, "running": running},
        )

    def _parse_list(self, utterance: str, lowered: str) -> SkillResult:
        category = self._detect_category(lowered)
        if category:
            apps = self.registry.by_category(category)
            label = category
        else:
            apps = self.registry.all_apps()
            label = "installed"
        if not apps:
            return SkillResult(
                text=f"I couldn't find any {label} applications.",
                skill=self.name,
                metadata={"operation": "list", "category": category, "count": 0},
            )
        names = [a.name for a in apps]
        shown = names[:10]
        listing = ", ".join(shown)
        more = "" if len(names) <= 10 else f", and {len(names) - 10} more"
        cat_word = f" {category}" if category else ""
        return SkillResult(
            text=f"I found {len(names)}{cat_word} apps: {listing}{more}.",
            skill=self.name,
            metadata={"operation": "list", "category": category, "count": len(names),
                      "apps": names},
        )

    # ------------------------------------------------------------------
    # Matching / extraction helpers
    # ------------------------------------------------------------------

    # Map spoken category words to freedesktop Category keys.
    _CATEGORY_SYNONYMS = {
        "browser": "WebBrowser",
        "browsers": "WebBrowser",
        "web browser": "WebBrowser",
        "editor": "TextEditor",
        "editors": "TextEditor",
        "text editor": "TextEditor",
        "game": "Game",
        "games": "Game",
        "office": "Office",
        "development": "Development",
        "developer": "Development",
        "graphics": "Graphics",
        "audio": "Audio",
        "video": "Video",
        "multimedia": "AudioVideo",
        "media": "AudioVideo",
        "utility": "Utility",
        "utilities": "Utility",
        "system": "System",
        "settings": "Settings",
        "network": "Network",
    }

    def _detect_category(self, lowered: str) -> Optional[str]:
        # Prefer multi-word matches first.
        for phrase in sorted(self._CATEGORY_SYNONYMS, key=len, reverse=True):
            if re.search(rf"\b{re.escape(phrase)}\b", lowered):
                return self._CATEGORY_SYNONYMS[phrase]
        return None

    def _extract_app_name(self, utterance: str, lowered: str, verbs) -> str:
        """Pull the application name out of a command utterance."""
        # Quoted name wins.
        q = re.search(r"[\"'“”‘’]([^\"'“”‘’]+)[\"'“”‘’]", utterance)
        if q:
            return q.group(1).strip()

        verb_alt = "|".join(re.escape(v) for v in verbs)
        token = utterance
        m = re.search(rf"^.*\b(?:{verb_alt})\b\s+(.+)$", utterance, re.IGNORECASE)
        if m:
            token = m.group(1)
        # Strip filler articles and trailing nouns/punctuation.
        token = re.sub(r"^(the|my|a|an|app|application|program)\s+", "", token, flags=re.IGNORECASE).strip()
        token = re.sub(
            r"\b(app|application|program|for me|please|now|right now)\b\.?$",
            "", token, flags=re.IGNORECASE,
        ).strip()
        token = re.sub(
            r"\b(is|are)\b.*$", "", token, flags=re.IGNORECASE
        ).strip() if token.lower().startswith(("is ", "are ")) else token
        token = re.sub(r"\b(running|open|active)\b\??$", "", token, flags=re.IGNORECASE).strip()
        return re.sub(r"[\s.?!,]+$", "", token).strip("\"'“”‘’ ")

    def _find_running(self, app: AppEntry) -> List[int]:
        """Return PIDs of processes that appear to belong to ``app``."""
        argv = app.argv()
        if not argv:
            return []
        exe_base = os.path.basename(argv[0]).lower()
        name_token = app.name.lower().split()[0] if app.name else ""
        pids: List[int] = []
        for proc in self._list_processes():
            pname = proc.get("name", "")
            pexe = os.path.basename(proc.get("exe", "")).lower()
            pcmd = proc.get("cmdline", "")
            if exe_base and (exe_base == pname or exe_base == pexe or exe_base in pcmd.split()):
                pid = proc.get("pid")
                if pid is not None:
                    pids.append(int(pid))
            elif name_token and len(name_token) > 2 and name_token == pname:
                pid = proc.get("pid")
                if pid is not None:
                    pids.append(int(pid))
        return pids

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if psutil is not None:
            try:
                return psutil.pid_exists(pid)
            except Exception:  # noqa: BLE001
                return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    @staticmethod
    def _default_terminator(pid: int, force: bool) -> bool:
        """Terminate ``pid`` gracefully, escalating to SIGKILL if needed."""
        if psutil is not None:
            try:
                proc = psutil.Process(pid)
                if force:
                    proc.kill()
                else:
                    proc.terminate()
                    try:
                        proc.wait(timeout=TERMINATE_TIMEOUT)
                    except psutil.TimeoutExpired:
                        proc.kill()
                return True
            except Exception:  # noqa: BLE001 - already gone / no permission
                return False
        # Fallback to os.kill.
        try:
            os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
            return True
        except OSError:
            return False

    def _suggestion(self, query: str) -> str:
        """Build a gentle "did you mean ..." hint from the closest matches."""
        ranked = self.registry.rank(query, limit=3)
        names = [app.name for app, score in ranked if score > 0.3]
        if not names:
            return ""
        if len(names) == 1:
            return f" Did you mean {names[0]}?"
        return f" Did you mean {', '.join(names[:-1])} or {names[-1]}?"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fail(self, message: str, *, operation: str = "unknown",
              extra: Optional[dict] = None) -> SkillResult:
        meta = {"operation": operation}
        if extra:
            meta.update(extra)
        return SkillResult(text=message, success=False, skill=self.name, metadata=meta)

    def _error_message(self) -> str:
        return "Sorry, I ran into a problem with that application command."
