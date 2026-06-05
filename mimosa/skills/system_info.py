"""System information skill -- answer questions about the host by voice (M2.3).

This skill lets the user interrogate their own machine conversationally:

* **Desktop / session** -- "what desktop am I using?", "is this Wayland or X11?",
  "what version of Plasma?".
* **Distro** -- "what operating system is this?", "what version of Kubuntu?".
* **Specs** -- "show me my system specs", "how much RAM do I have?", "how many
  CPU cores?", "what graphics card?".
* **Audio** -- "what audio backend am I using?", "do I have a microphone?".
* **Recommendations** -- "what settings do you recommend for this machine?".

It is built on the local :class:`~mimosa.system.system_profiler.SystemProfiler`,
:class:`~mimosa.system.hardware_detector.HardwareDetector`, and
:class:`~mimosa.system.system_optimizer.SystemOptimizer`, so it is fast,
deterministic, and entirely offline.

Design principles
-----------------
* **Local & private.** ``uses_llm`` is ``False``. No hardware or OS details are
  ever sent to the cloud -- the whole point is to keep this on-device.
* **Read-only.** These are pure queries; there is no destructive action and
  therefore no confirmation flow.
* **Graceful.** Missing facts are reported honestly ("I couldn't determine
  that") rather than guessed.
"""

from __future__ import annotations

import re
from typing import List, Optional

from mimosa.skills.base_skill import BaseSkill, SkillResult
from mimosa.system.hardware_detector import HardwareDetector
from mimosa.system.system_optimizer import SystemOptimizer
from mimosa.system.system_profiler import SystemProfiler


class SystemInfoSkill(BaseSkill):
    """Answer natural-language questions about the host system."""

    name = "system_info"
    intents = ["system_info"]
    uses_llm = False

    def __init__(
        self,
        llm_provider=None,
        *,
        profiler: Optional[SystemProfiler] = None,
        hardware: Optional[HardwareDetector] = None,
        optimizer: Optional[SystemOptimizer] = None,
    ) -> None:
        """Construct the skill.

        Args:
            llm_provider: Unused (uniform constructor); this is a local skill.
            profiler: A :class:`SystemProfiler`; defaults to a real one.
            hardware: A :class:`HardwareDetector`; defaults to a real one.
            optimizer: A :class:`SystemOptimizer`; defaults to one wired to the
                provided profiler/hardware so all three share a view.
        """
        super().__init__(llm_provider=llm_provider)
        self.profiler = profiler or SystemProfiler()
        self.hardware = hardware or HardwareDetector()
        self.optimizer = optimizer or SystemOptimizer(
            profiler=self.profiler, hardware=self.hardware
        )

    # ------------------------------------------------------------------
    # NL entry point
    # ------------------------------------------------------------------

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        lowered = (text or "").strip().lower()
        if not lowered:
            return self._fail("What would you like to know about your system?")

        # Order matters: most specific topics first, full-specs catch-all last.
        if self._asks(lowered, r"\b(display server|wayland|x11|x ?eleven|xorg)\b"):
            return self._answer_display_server()
        if self._asks(lowered, r"\b(plasma|kde version)\b") or (
            "version" in lowered and "plasma" in lowered
        ):
            return self._answer_plasma()
        if self._asks(lowered, r"\b(desktop( environment)?|de|gnome|kde|window manager)\b"):
            return self._answer_desktop()
        if self._asks(lowered, r"\b(audio|sound) (backend|server|system)\b") or self._asks(
            lowered, r"\bwhat.*(audio|sound)\b"
        ):
            return self._answer_audio()
        if self._asks(lowered, r"\b(microphone|mic|recording device)\b"):
            return self._answer_microphone()
        if self._asks(lowered, r"\b(ram|memory)\b"):
            return self._answer_memory()
        if self._asks(lowered, r"\b(cpu|processor|cores?|threads?)\b"):
            return self._answer_cpu()
        if self._asks(lowered, r"\b(gpu|graphics|video card|graphics card)\b"):
            return self._answer_gpu()
        if self._asks(lowered, r"\b(display|monitor|screen|resolution)\b"):
            return self._answer_displays()
        if self._asks(lowered, r"\b(kernel|architecture|arch)\b"):
            return self._answer_kernel()
        if self._asks(lowered, r"\b(distro|distribution|operating system|os|kubuntu|ubuntu|linux)\b"):
            return self._answer_distro()
        if self._asks(lowered, r"\b(recommend|optimi[sz]e|settings|tune|tuning|performance)\b"):
            return self._answer_recommendation()
        if self._asks(lowered, r"\b(spec|specs|specifications|system info|about (this|my) (system|computer|machine|pc)|hardware)\b"):
            return self._answer_full_specs()

        # Generic "what system am I on" style -> full specs.
        return self._answer_full_specs()

    # ------------------------------------------------------------------
    # Answers
    # ------------------------------------------------------------------

    def _answer_distro(self) -> SkillResult:
        p = self.profiler.profile
        if not (p.distro_name or p.distro_id):
            return self._fail("I couldn't determine the operating system.")
        name = p.distro_name or p.distro_id
        extra = " This is a Kubuntu system." if p.is_kubuntu else ""
        return self._ok(f"You're running {name}.{extra}", {"distro": name, "is_kubuntu": p.is_kubuntu})

    def _answer_desktop(self) -> SkillResult:
        p = self.profiler.profile
        if not p.desktop_environment:
            return self._fail("I couldn't determine your desktop environment.")
        de = p.desktop_environment
        if de == "KDE":
            ver = f" Plasma {p.plasma_version}" if p.plasma_version else ""
            return self._ok(f"You're using the KDE{ver} desktop environment.", {"desktop": de, "plasma": p.plasma_version})
        return self._ok(f"You're using the {de} desktop environment.", {"desktop": de})

    def _answer_display_server(self) -> SkillResult:
        p = self.profiler.profile
        if not p.display_server:
            return self._fail("I couldn't determine whether you're on Wayland or X11.")
        pretty = "Wayland" if p.display_server == "wayland" else "X11"
        return self._ok(f"Your display server is {pretty}.", {"display_server": p.display_server})

    def _answer_plasma(self) -> SkillResult:
        p = self.profiler.profile
        if p.desktop_environment != "KDE":
            return self._ok(
                "This isn't a KDE Plasma session, so there's no Plasma version to report.",
                {"is_kde": False},
            )
        if not p.plasma_version:
            return self._ok("You're on KDE Plasma, but I couldn't determine the exact version.", {"is_kde": True})
        return self._ok(f"You're running KDE Plasma version {p.plasma_version}.", {"plasma": p.plasma_version})

    def _answer_audio(self) -> SkillResult:
        hw = self.hardware.profile
        if not hw.audio.backend:
            return self._fail("I couldn't detect an audio backend on this system.")
        state = "running" if hw.audio.server_running else "installed but not confirmed running"
        return self._ok(
            f"Your audio backend is {hw.audio.backend} ({state}).",
            {"backend": hw.audio.backend, "running": hw.audio.server_running},
        )

    def _answer_microphone(self) -> SkillResult:
        hw = self.hardware.profile
        if not hw.has_microphone:
            return self._ok("I didn't detect any microphone on this system.", {"microphones": []})
        count = len(hw.microphones)
        first = hw.microphones[0]
        if count == 1:
            return self._ok(f"You have one microphone available: {first}.", {"microphones": hw.microphones})
        return self._ok(
            f"You have {count} microphones available, including {first}.",
            {"microphones": hw.microphones},
        )

    def _answer_memory(self) -> SkillResult:
        hw = self.hardware.profile
        if hw.memory.total_gb is None:
            return self._fail("I couldn't determine how much memory you have.")
        avail = (
            f" with {hw.memory.available_gb:g} GB currently available"
            if hw.memory.available_gb is not None else ""
        )
        return self._ok(
            f"You have {hw.memory.total_gb:g} GB of RAM{avail}.",
            {"total_gb": hw.memory.total_gb, "available_gb": hw.memory.available_gb},
        )

    def _answer_cpu(self) -> SkillResult:
        hw = self.hardware.profile
        cpu = hw.cpu
        if not (cpu.logical_cores or cpu.model):
            return self._fail("I couldn't determine your CPU details.")
        bits = []
        if cpu.model:
            bits.append(cpu.model)
        if cpu.physical_cores and cpu.logical_cores:
            bits.append(f"{cpu.physical_cores} cores / {cpu.logical_cores} threads")
        elif cpu.logical_cores:
            bits.append(f"{cpu.logical_cores} logical cores")
        if cpu.max_frequency_mhz:
            bits.append(f"up to {cpu.max_frequency_mhz / 1000:.1f} GHz")
        return self._ok("Your CPU is " + ", ".join(bits) + ".", {
            "model": cpu.model,
            "physical_cores": cpu.physical_cores,
            "logical_cores": cpu.logical_cores,
        })

    def _answer_gpu(self) -> SkillResult:
        hw = self.hardware.profile
        if not hw.gpus:
            return self._fail("I couldn't detect a graphics adapter.")
        descs = []
        for g in hw.gpus:
            descs.append(g.description or g.vendor or "an unidentified GPU")
        if len(descs) == 1:
            return self._ok(f"Your graphics adapter is {descs[0]}.", {"gpus": descs})
        return self._ok("You have multiple graphics adapters: " + "; ".join(descs) + ".", {"gpus": descs})

    def _answer_displays(self) -> SkillResult:
        hw = self.hardware.profile
        if not hw.displays:
            return self._fail("I couldn't detect any connected displays.")
        count = len(hw.displays)
        details = []
        for d in hw.displays:
            label = d.name or "display"
            if d.resolution:
                label += f" at {d.resolution}"
            if d.primary:
                label += " (primary)"
            details.append(label)
        head = f"You have {count} display" + ("s" if count != 1 else "") + ": "
        return self._ok(head + "; ".join(details) + ".", {"count": count, "displays": details})

    def _answer_kernel(self) -> SkillResult:
        p = self.profiler.profile
        bits = []
        if p.kernel:
            bits.append(f"Linux kernel {p.kernel}")
        if p.architecture:
            bits.append(f"on {p.architecture}")
        if not bits:
            return self._fail("I couldn't determine your kernel or architecture.")
        return self._ok("You're running " + " ".join(bits) + ".", {
            "kernel": p.kernel, "architecture": p.architecture,
        })

    def _answer_recommendation(self) -> SkillResult:
        cfg = self.optimizer.config
        msg = (
            f"For this {cfg.performance_tier}-performance machine I recommend the "
            f"{cfg.whisper_model} speech model, {cfg.tts_quality} voice quality, "
            f"a wake-word sensitivity of {cfg.wake_word_sensitivity}, and keeping "
            f"up to {cfg.max_history_turns} conversation turns."
        )
        if cfg.audio_backend:
            msg += f" Audio will use the {cfg.audio_backend} backend."
        return self._ok(msg, cfg.as_dict())

    def _answer_full_specs(self) -> SkillResult:
        p = self.profiler.profile
        hw = self.hardware.profile
        os_part = p.summary()
        hw_part = hw.summary()
        return self._ok(
            f"Here are your system specs. {os_part}. {hw_part}.",
            {"system": p.as_dict(), "hardware": hw.as_dict()},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _asks(lowered: str, pattern: str) -> bool:
        return re.search(pattern, lowered) is not None

    def _ok(self, text: str, metadata: Optional[dict] = None) -> SkillResult:
        return SkillResult(text=text, success=True, skill=self.name, metadata=metadata or {})

    def _fail(self, message: str) -> SkillResult:
        return SkillResult(text=message, success=False, skill=self.name, metadata={})

    def _error_message(self) -> str:
        return "Sorry, I ran into a problem reading your system information."
