"""Weather skill -- current conditions via the free wttr.in service.

This skill fetches live weather from `wttr.in <https://wttr.in>`_, a free
service that needs **no API key**. A location can be supplied in the utterance
("weather in Tokyo") or configured via the ``DEFAULT_LOCATION`` env var; if
neither is given, wttr.in auto-detects from the request IP.

Privacy / network note
----------------------
Unlike the time and calculator skills, this one **does** make a network request
(to wttr.in). It does *not* use the LLM. If the service is unreachable or slow,
the skill degrades gracefully with a clear spoken message rather than raising.

An optional ``WEATHER_API_KEY`` / OpenWeatherMap path is intentionally left as a
future enhancement; wttr.in keeps the default zero-config and key-free.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

import requests

from mimosa.skills.base_skill import BaseSkill, SkillResult

WTTR_BASE_URL = "https://wttr.in"
DEFAULT_TIMEOUT = 8.0


def _extract_location(text: str) -> Optional[str]:
    """Pull a location out of phrases like 'weather in Paris' / 'weather for NYC'."""
    if not text:
        return None
    match = re.search(r"\b(?:in|for|at)\s+([A-Za-z][A-Za-z\s,.'-]+)\??$", text.strip())
    if match:
        loc = match.group(1).strip(" ?.")
        # Avoid capturing filler like "today"/"now".
        loc = re.sub(r"\b(today|now|right now|currently|please)\b", "", loc, flags=re.I).strip()
        return loc or None
    return None


class WeatherSkill(BaseSkill):
    """Report current weather using the key-free wttr.in service."""

    name = "weather"
    intents = ["weather"]
    uses_llm = False

    def __init__(self, llm_provider=None, timeout: float = DEFAULT_TIMEOUT) -> None:
        super().__init__(llm_provider=llm_provider)
        self.timeout = timeout

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        location = _extract_location(text) or os.getenv("DEFAULT_LOCATION") or ""
        return self._fetch_weather(location)

    def _fetch_weather(self, location: str) -> SkillResult:
        # wttr.in format=j1 returns compact JSON; %-encoded path is the location.
        url = f"{WTTR_BASE_URL}/{requests.utils.quote(location)}?format=j1"
        try:
            resp = requests.get(url, timeout=self.timeout, headers={"User-Agent": "curl"})
        except requests.RequestException as exc:
            self.logger.warning("Weather request failed: %s", exc)
            return self._unavailable(location, str(exc))

        if resp.status_code >= 400:
            return self._unavailable(location, f"HTTP {resp.status_code}")

        try:
            data = resp.json()
            current = data["current_condition"][0]
            temp_c = current["temp_C"]
            temp_f = current["temp_F"]
            desc = current["weatherDesc"][0]["value"]
            feels_c = current.get("FeelsLikeC", temp_c)
        except (ValueError, KeyError, IndexError) as exc:
            self.logger.warning("Could not parse weather payload: %s", exc)
            return self._unavailable(location, "unparseable response")

        where = self._describe_location(data, location)
        text = (
            f"The weather{where} is {desc.lower()}, {temp_c}°C "
            f"({temp_f}°F), feels like {feels_c}°C."
        )
        return SkillResult(
            text=text,
            skill=self.name,
            metadata={
                "location": location or "auto",
                "temp_c": temp_c,
                "temp_f": temp_f,
                "description": desc,
            },
        )

    @staticmethod
    def _describe_location(data: dict, fallback: str) -> str:
        """Build a ' in <place>' clause from the API payload when available."""
        try:
            area = data["nearest_area"][0]
            name = area["areaName"][0]["value"]
            return f" in {name}"
        except (KeyError, IndexError):
            return f" in {fallback}" if fallback else ""

    def _unavailable(self, location: str, reason: str) -> SkillResult:
        where = f" for {location}" if location else ""
        return SkillResult(
            text=(
                f"I couldn't get the weather{where} right now. "
                "The weather service may be unavailable. Please try again later."
            ),
            success=False,
            skill=self.name,
            metadata={"location": location or "auto", "error": reason},
        )

    def _error_message(self) -> str:
        return "I couldn't get the weather right now. Please try again later."
