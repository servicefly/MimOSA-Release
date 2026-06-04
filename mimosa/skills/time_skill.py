"""Time and date skill -- answers clock/calendar questions locally.

This skill is **fully local**: it reads the system clock and never contacts the
network or the LLM. That makes it instant, free, and privacy-preserving, and it
demonstrates the principle that simple intents should be handled with local
logic rather than a round-trip to the cloud.

Handled questions include:
    * "What time is it?"
    * "What's today's date?"
    * "What day is it?"
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from mimosa.skills.base_skill import BaseSkill, SkillResult


class TimeSkill(BaseSkill):
    """Answer time/date queries from the system clock (no API/LLM)."""

    name = "time"
    intents = ["time", "date"]
    uses_llm = False

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        now = datetime.now()
        lowered = (text or "").lower()

        wants_date = any(k in lowered for k in ("date", "today", "day", "month", "year"))
        wants_time = any(k in lowered for k in ("time", "clock", "hour", "o'clock"))

        # If the user explicitly asked for the day of the week.
        if "day" in lowered and "today" not in lowered and not wants_time:
            day_name = now.strftime("%A")
            return SkillResult(
                text=f"Today is {day_name}.",
                skill=self.name,
                metadata={"weekday": day_name},
            )

        if wants_date and not wants_time:
            date_str = now.strftime("%A, %B %d, %Y")
            return SkillResult(
                text=f"Today is {date_str}.",
                skill=self.name,
                metadata={"date": now.isoformat()},
            )

        if wants_time and not wants_date:
            time_str = now.strftime("%-I:%M %p") if hasattr(now, "strftime") else ""
            # %-I is platform-dependent; fall back safely.
            try:
                time_str = now.strftime("%-I:%M %p")
            except ValueError:
                time_str = now.strftime("%I:%M %p").lstrip("0")
            return SkillResult(
                text=f"It's {time_str}.",
                skill=self.name,
                metadata={"time": now.isoformat()},
            )

        # Ambiguous or asked for both -- give the full picture.
        try:
            time_str = now.strftime("%-I:%M %p")
        except ValueError:
            time_str = now.strftime("%I:%M %p").lstrip("0")
        date_str = now.strftime("%A, %B %d, %Y")
        return SkillResult(
            text=f"It's {time_str} on {date_str}.",
            skill=self.name,
            metadata={"datetime": now.isoformat()},
        )
