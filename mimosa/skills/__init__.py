"""Skills package for MimOSA.

Skills are the concrete handlers MimOSA uses to satisfy a classified intent.
Each skill subclasses :class:`~mimosa.skills.base_skill.BaseSkill` and exposes a
uniform :meth:`run`/:meth:`handle` interface so the
:class:`~mimosa.core.intent_router.IntentRouter` can dispatch to the right one.

Skills shipped in M1.3 (basic command set):

* :class:`~mimosa.skills.time_skill.TimeSkill` -- time/date (local, no LLM).
* :class:`~mimosa.skills.calculator_skill.CalculatorSkill` -- safe arithmetic
  (local, no LLM, AST-based -- never ``eval``).
* :class:`~mimosa.skills.weather_skill.WeatherSkill` -- current weather via the
  key-free wttr.in service (network, no LLM).
* :class:`~mimosa.skills.question_skill.QuestionSkill` -- general knowledge via
  the LLM.
* :class:`~mimosa.skills.greeting_skill.GreetingSkill` -- greetings/chit-chat
  via the LLM, with local fallbacks.

Privacy: local skills (time, calculator) never touch the network; LLM-backed
skills send only transcribed *text* to the provider, and route through a local
provider when the Privacy Guard forces local-only mode.

Future modules: ``file_ops.py``, ``app_launcher.py``, ``research.py``,
``system_control.py``, and ``code_gen.py``.
"""

from mimosa.skills.application import ApplicationSkill
from mimosa.skills.base_skill import BaseSkill, SkillResult
from mimosa.skills.calculator_skill import CalculatorSkill
from mimosa.skills.file_ops import FileOperationsSkill
from mimosa.skills.greeting_skill import GreetingSkill
from mimosa.skills.question_skill import QuestionSkill
from mimosa.skills.system_control import SystemControlSkill
from mimosa.skills.system_info import SystemInfoSkill
from mimosa.skills.time_skill import TimeSkill
from mimosa.skills.weather_skill import WeatherSkill

__all__ = [
    "BaseSkill",
    "SkillResult",
    "TimeSkill",
    "CalculatorSkill",
    "WeatherSkill",
    "QuestionSkill",
    "GreetingSkill",
    "FileOperationsSkill",
    "ApplicationSkill",
    "SystemControlSkill",
    "SystemInfoSkill",
]
