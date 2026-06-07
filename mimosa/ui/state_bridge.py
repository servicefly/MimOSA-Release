"""Thread-safe bridge between the voice loop and the GTK avatar (M3.1).

The voice loop runs on a **worker thread** (so the GTK main loop stays
responsive). GTK/GDK are *not* thread-safe: any widget mutation must happen on
the main thread. :class:`StateBridge` is the seam that makes that safe.

Flow::

    VoiceLoop (worker thread)            StateBridge                GTK main thread
    --------------------------           -----------                ---------------
    _set_state(LISTENING) ----notify---> map -> UIState
                                         GLib.idle_add(...)  ------> on_state_change(UIState)
                                                                     -> avatar.set_state(...)

Key properties
--------------
* **Non-blocking.** ``notify`` returns immediately; the actual UI callback is
  queued on the GTK main loop via ``GLib.idle_add`` and runs there.
* **GLib is injectable.** Tests pass a fake ``glib`` whose ``idle_add`` runs the
  callback synchronously, so transitions can be asserted without a real GTK
  loop. When GLib can't be imported (headless), the bridge falls back to
  invoking the callback directly -- still safe, just synchronous.
* **Decoupled from voice internals.** The bridge maps the voice
  ``VoiceState`` to a UI-facing :class:`UIState` by *value string*, so it never
  has to import the voice package (avoids a heavy import on headless installs).
"""

from __future__ import annotations

import enum
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class UIState(enum.Enum):
    """Visual states the avatar can render.

    Mirrors the voice loop's states one-to-one but lives in the UI layer so the
    renderer/window never import the voice package.
    """

    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    PAUSED = "paused"      # listening suspended by the user (mic muted)
    DISABLED = "disabled"  # loop stopped / not running

    @classmethod
    def from_voice_state(cls, voice_state) -> "UIState":
        """Map a voice ``VoiceState`` (or its ``.value``/name) to a ``UIState``.

        Accepts the enum, a raw string value (``"listening"``), or anything with
        a ``.value`` attribute. Unknown values map to :attr:`IDLE` so the UI
        always has something sensible to show.
        """
        value = getattr(voice_state, "value", voice_state)
        if isinstance(value, str):
            value = value.lower()
        mapping = {
            "idle": cls.IDLE,
            "listening": cls.LISTENING,
            "processing": cls.PROCESSING,
            "speaking": cls.SPEAKING,
            "paused": cls.PAUSED,
            "stopped": cls.DISABLED,
        }
        return mapping.get(value, cls.IDLE)


def _import_glib():
    """Best-effort import of ``gi.repository.GLib``; returns ``None`` if absent."""
    try:
        import gi  # noqa: F401
        from gi.repository import GLib

        return GLib
    except Exception:  # pragma: no cover - exercised only on headless boxes
        return None


class StateBridge:
    """Marshals voice-loop state changes onto the GTK main thread.

    Args:
        on_state_change: Callable invoked (on the GTK main thread) with the new
            :class:`UIState` whenever the voice state changes. Optional so the
            bridge can be constructed before the window exists; set it later via
            :attr:`on_state_change`.
        glib: Injectable GLib-like object exposing ``idle_add(callable)``. When
            ``None``, the real ``gi.repository.GLib`` is used if importable;
            otherwise the callback runs synchronously.
    """

    def __init__(
        self,
        on_state_change: Optional[Callable[["UIState"], None]] = None,
        glib=None,
    ) -> None:
        self.on_state_change = on_state_change
        self._glib = glib if glib is not None else _import_glib()
        self._current = UIState.IDLE
        self._voice_loop = None

    @property
    def current_state(self) -> UIState:
        """The most recently observed :class:`UIState`."""
        return self._current

    @property
    def uses_glib(self) -> bool:
        """True if a GLib object is available to marshal onto the main loop."""
        return self._glib is not None

    # -- subscription ------------------------------------------------------

    def subscribe(self, voice_loop) -> None:
        """Register this bridge as a state listener on ``voice_loop``.

        Safe to call when ``voice_loop`` is ``None`` (no-op). Stores the loop so
        :meth:`unsubscribe` can detach cleanly on shutdown.
        """
        if voice_loop is None:
            return
        self._voice_loop = voice_loop
        try:
            voice_loop.add_state_listener(self.notify)
        except AttributeError:
            logger.warning("Voice loop has no add_state_listener; UI will not track state")

    def unsubscribe(self) -> None:
        """Detach from the subscribed voice loop (safe if never subscribed)."""
        if self._voice_loop is not None:
            try:
                self._voice_loop.remove_state_listener(self.notify)
            except AttributeError:  # pragma: no cover
                pass
            self._voice_loop = None

    # -- the listener callback (called on the WORKER thread) ---------------

    def notify(self, voice_state) -> None:
        """Receive a voice-state change (worker thread) and queue a UI update.

        This is the function registered with ``VoiceLoop.add_state_listener``.
        It maps the state and schedules :meth:`_dispatch` on the GTK main loop
        via ``GLib.idle_add`` (or runs it directly when no GLib is available).
        Never raises -- the voice loop must not be affected by UI problems.
        """
        try:
            ui_state = UIState.from_voice_state(voice_state)
        except Exception as exc:  # pragma: no cover - mapping is total, defensive
            logger.error("Failed to map voice state %r: %s", voice_state, exc)
            return

        if self._glib is not None:
            try:
                # idle_add callbacks must return False to fire once.
                self._glib.idle_add(self._dispatch, ui_state)
                return
            except Exception as exc:  # pragma: no cover - defensive
                logger.error("idle_add failed (%s); dispatching synchronously", exc)
        # Headless / no-GLib fallback: dispatch immediately on this thread.
        self._dispatch(ui_state)

    def _dispatch(self, ui_state: "UIState") -> bool:
        """Run the UI callback on the GTK main thread. Returns ``False`` (run once)."""
        self._current = ui_state
        cb = self.on_state_change
        if cb is not None:
            try:
                cb(ui_state)
            except Exception as exc:
                logger.error("UI state callback failed: %s", exc)
        return False  # GLib.idle_add contract: returning False removes the source
