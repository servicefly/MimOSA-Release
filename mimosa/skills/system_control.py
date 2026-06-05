"""System control skill -- volume, brightness, Wi-Fi and battery by voice (M2.2).

This skill turns natural-language system commands into actions via the
backend-agnostic :class:`~mimosa.system.system_control.SystemController`:

* **Volume** -- "turn the volume up", "set volume to 30 percent", "mute".
* **Brightness** -- "brightness down", "set brightness to 70 percent".
* **Wi-Fi** -- "turn wifi off", "is my wifi on?".
* **Battery** -- "how much battery do I have left?".

Design principles
-----------------
* **Local & private.** ``uses_llm`` is ``False``; everything is a local
  subprocess call or a ``/sys`` read.
* **Graceful degradation.** If the underlying tool isn't installed, the
  controller returns a clean "not available" message that the skill speaks
  verbatim -- never a crash.
* **Confirm disruptive changes.** Turning Wi-Fi *off* can drop the user's
  connection, so it is treated as a state-changing action and confirmed first
  (mirroring the file-ops/app-close safety pattern). Reversible, low-impact
  changes (volume/brightness) act immediately.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from mimosa.skills.base_skill import BaseSkill, SkillResult
from mimosa.system.system_control import CommandResult, SystemController

_CONFIRM_WORDS = re.compile(
    r"^\s*(yes|yep|yeah|yup|confirm|confirmed|do it|go ahead|proceed|sure|ok|okay|"
    r"affirmative|please do)\b",
    re.IGNORECASE,
)
_CANCEL_WORDS = re.compile(
    r"^\s*(no|nope|cancel|stop|don'?t|never ?mind|abort|forget it)\b",
    re.IGNORECASE,
)

#: Default step size for relative volume/brightness changes (percent).
DEFAULT_STEP = 10


@dataclass
class _PendingAction:
    description: str
    execute: Callable[[], SkillResult]
    created: float = field(default_factory=time.time)


class SystemControlSkill(BaseSkill):
    """Adjust volume, brightness, Wi-Fi and report battery by voice."""

    name = "system_control"
    intents = ["system_control", "system"]
    uses_llm = False

    def __init__(
        self,
        llm_provider=None,
        *,
        controller: Optional[SystemController] = None,
        step: int = DEFAULT_STEP,
    ) -> None:
        """Construct the skill.

        Args:
            llm_provider: Unused (uniform constructor); local skill.
            controller: A :class:`SystemController`. Defaults to a real one;
                tests inject a fake-backed controller.
            step: Default percentage step for relative up/down commands.
        """
        super().__init__(llm_provider=llm_provider)
        self.controller = controller or SystemController()
        self.step = step
        self._pending: Optional[_PendingAction] = None

    # ------------------------------------------------------------------
    # NL entry point
    # ------------------------------------------------------------------

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        utterance = (text or "").strip()
        lowered = utterance.lower()

        if self._pending is not None:
            return self._resolve_pending(lowered)

        if not lowered:
            return self._fail("I didn't catch a system command.")

        # Battery
        if re.search(r"\bbattery\b|\bcharge\b|\bpower\b", lowered):
            return self._wrap(self.controller.get_battery(), "battery")

        # Wi-Fi
        if re.search(r"\b(wi-?fi|wireless|internet|network)\b", lowered):
            return self._parse_wifi(lowered)

        # Brightness
        if re.search(r"\bbright(ness)?\b|\bdim\b|\bscreen\b", lowered):
            return self._parse_brightness(lowered)

        # Volume / mute (check last; "volume", "sound", "audio", "louder"...)
        if re.search(r"\b(volume|sound|audio|loud(er)?|quiet(er)?|mute|unmute)\b", lowered):
            return self._parse_volume(lowered)

        return self._fail(
            "I can adjust the volume, screen brightness, or Wi-Fi, and tell you "
            "the battery level. What would you like?"
        )

    # ------------------------------------------------------------------
    # Confirmation
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
    # Parsers
    # ------------------------------------------------------------------

    def _parse_volume(self, lowered: str) -> SkillResult:
        if re.search(r"\bunmute\b", lowered):
            return self._wrap(self.controller.set_mute(False), "volume")
        if re.search(r"\bmute\b|\bsilence\b", lowered):
            return self._wrap(self.controller.set_mute(True), "volume")

        percent = self._extract_percent(lowered)
        if percent is not None and re.search(r"\b(set|make|change|to|at)\b", lowered):
            return self._wrap(self.controller.set_volume(percent), "volume")

        if re.search(r"\b(up|increase|raise|louder|higher|more)\b", lowered):
            step = percent if percent is not None else self.step
            return self._wrap(self.controller.change_volume(step), "volume")
        if re.search(r"\b(down|decrease|lower|quieter|softer|less)\b", lowered):
            step = percent if percent is not None else self.step
            return self._wrap(self.controller.change_volume(-step), "volume")

        if percent is not None:
            return self._wrap(self.controller.set_volume(percent), "volume")

        # Bare query: "what's the volume?"
        return self._wrap(self.controller.get_volume(), "volume")

    def _parse_brightness(self, lowered: str) -> SkillResult:
        percent = self._extract_percent(lowered)
        if percent is not None and re.search(r"\b(set|make|change|to|at)\b", lowered):
            return self._wrap(self.controller.set_brightness(percent), "brightness")

        if re.search(r"\b(up|increase|raise|brighter|higher|more)\b", lowered):
            step = percent if percent is not None else self.step
            return self._wrap(self.controller.change_brightness(step), "brightness")
        if re.search(r"\b(down|decrease|lower|dimmer|dim|darker|less)\b", lowered):
            step = percent if percent is not None else self.step
            return self._wrap(self.controller.change_brightness(-step), "brightness")

        if percent is not None:
            return self._wrap(self.controller.set_brightness(percent), "brightness")

        return self._wrap(self.controller.get_brightness(), "brightness")

    def _parse_wifi(self, lowered: str) -> SkillResult:
        # Status query.
        if re.search(r"\b(is|are|status|connected|what|which)\b", lowered) and not re.search(
            r"\b(turn|switch|enable|disable)\b", lowered
        ):
            return self._wrap(self.controller.get_wifi_status(), "wifi")

        if re.search(r"\b(on|enable|enabled|connect)\b", lowered):
            return self._wrap(self.controller.set_wifi(True), "wifi")
        if re.search(r"\b(off|disable|disabled|disconnect)\b", lowered):
            # Turning Wi-Fi off can drop connectivity -- confirm first.
            return self._queue(
                "Turning off Wi-Fi will disconnect you from the network.",
                lambda: self._wrap(self.controller.set_wifi(False), "wifi"),
            )
        # Default to a status report.
        return self._wrap(self.controller.get_wifi_status(), "wifi")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_percent(lowered: str) -> Optional[int]:
        """Pull a 0-100 percentage out of an utterance, if present."""
        m = re.search(r"(\d{1,3})\s*(?:percent|%)", lowered)
        if not m:
            # A bare number after set/to (e.g. "set volume to 40").
            m = re.search(r"\b(?:to|at|set\w*)\s+(\d{1,3})\b", lowered)
        if not m:
            return None
        try:
            value = int(m.group(1))
        except ValueError:
            return None
        return max(0, min(100, value))

    def _wrap(self, result: CommandResult, domain: str) -> SkillResult:
        """Convert a :class:`CommandResult` into a :class:`SkillResult`."""
        meta = {"operation": domain, **result.data}
        return SkillResult(
            text=result.message,
            success=result.success,
            skill=self.name,
            metadata=meta,
        )

    def _fail(self, message: str, *, operation: str = "unknown") -> SkillResult:
        return SkillResult(
            text=message, success=False, skill=self.name,
            metadata={"operation": operation},
        )

    def _error_message(self) -> str:
        return "Sorry, I ran into a problem with that system command."
