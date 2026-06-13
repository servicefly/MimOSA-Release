"""High-level onboarding orchestration for MimOSA (M3).

:class:`OnboardingManager` ties the conversation engine, fact extractor,
profile manager, and vector store together and adds the cross-cutting
concerns the UI cares about:

* **pause / resume** — the full conversation state is persisted to
  ``~/.local/share/mimosa/onboarding_state.json`` so an abandoned onboarding
  can be picked up exactly where it left off.
* **should_run()** — decide whether onboarding should be offered, based on the
  user's saved preference / completion flag.
* **completion** — finalise the profile, mark config complete, build a warm
  summary, and clear the resume state.

The manager wires graceful fallbacks throughout: a missing LLM falls back to
heuristic fact extraction, a missing/!available Chroma falls back to JSON
(handled inside :class:`MemoryVectorStore`), and persistence errors are
swallowed so they never block the conversation.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Dict, List, Optional

from mimosa.memory.paths import onboarding_state_path
from mimosa.memory.profile_manager import ProfileManager
from mimosa.onboarding.conversation_engine import OnboardingConversation
from mimosa.onboarding.fact_extractor import FactExtractor

__all__ = ["OnboardingManager"]


class OnboardingManager:
    """Orchestrate the onboarding conversation with pause/resume support."""

    def __init__(
        self,
        *,
        llm: Any = None,
        profile_manager: Optional[ProfileManager] = None,
        vector_store: Any = None,
        fact_extractor: Any = None,
        state_path: Optional[str] = None,
        config_manager: Any = None,
    ):
        self.llm = llm
        self.config_manager = config_manager
        self.vector_store = vector_store
        self.profile_manager = profile_manager or ProfileManager(
            vector_store=vector_store
        )
        self.fact_extractor = fact_extractor or FactExtractor(llm=llm)
        self.state_path = state_path or str(onboarding_state_path())

        self.conversation = OnboardingConversation(
            fact_extractor=self.fact_extractor,
            profile_manager=self.profile_manager,
            vector_store=self.vector_store,
        )
        self._completed = False

    # -- lifecycle decisions ---------------------------------------------
    def should_run(self) -> bool:
        """Return True when onboarding should be offered/run.

        Honours the user's saved personality preference: skip if already
        complete or explicitly set to ``"skip"``.
        """

        cfg = self._personality()
        if cfg is None:
            return not self.is_complete_flag()
        if getattr(cfg, "onboarding_complete", False):
            return False
        pref = getattr(cfg, "onboarding_preference", "later")
        return pref != "skip"

    def is_complete_flag(self) -> bool:
        cfg = self._personality()
        return bool(getattr(cfg, "onboarding_complete", False)) if cfg else False

    def has_saved_state(self) -> bool:
        """True when a resumable onboarding state file exists."""

        return os.path.exists(self.state_path)

    # -- running ----------------------------------------------------------
    def begin(self, *, resume: bool = True):
        """Start onboarding, resuming from disk when available.

        Returns the first :class:`Prompt` to present.
        """

        if resume and self.has_saved_state():
            self.resume()
        return self.conversation.start()

    def current_prompt(self):
        return self.conversation.current_prompt()

    def submit(self, text: str, *, autosave: bool = True) -> Dict[str, Any]:
        """Submit a response; persist resume state unless finished."""

        result = self.conversation.submit_response(text)
        if self.conversation.is_complete:
            self.complete()
        elif autosave:
            self.save_state()
        return result

    def skip_topic(self, *, autosave: bool = True):
        prompt = self.conversation.skip_topic()
        if self.conversation.is_complete:
            self.complete()
        elif autosave:
            self.save_state()
        return prompt

    @property
    def is_complete(self) -> bool:
        return self._completed or self.conversation.is_complete

    @property
    def progress(self) -> float:
        return self.conversation.progress

    @property
    def transcript(self):
        return self.conversation.transcript

    # -- pause / resume persistence --------------------------------------
    def save_state(self) -> bool:
        """Persist conversation + partial profile to disk. Never raises."""

        try:
            payload = {
                "version": 1,
                "saved_at": time.time(),
                "conversation": self.conversation.to_state(),
                "profile": self.profile_manager.to_dict(),
            }
            directory = os.path.dirname(self.state_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=".onboarding_", suffix=".tmp", dir=directory or None
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2)
                os.replace(tmp, self.state_path)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            return True
        except Exception:
            return False

    def resume(self) -> bool:
        """Load saved state from disk into the live conversation/profile."""

        try:
            with open(self.state_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception:
            return False
        try:
            convo_state = payload.get("conversation")
            if isinstance(convo_state, dict):
                self.conversation.load_state(convo_state)
            profile = payload.get("profile")
            if isinstance(profile, dict):
                # Merge saved profile back in (engine references the same pm).
                from mimosa.memory.profile_manager import UserProfile

                self.profile_manager.profile = UserProfile.from_dict(profile)
            return True
        except Exception:
            return False

    def clear_state(self) -> None:
        """Remove the resume file (best-effort)."""

        try:
            if os.path.exists(self.state_path):
                os.remove(self.state_path)
        except OSError:
            pass

    # -- completion -------------------------------------------------------
    def complete(self) -> Dict[str, Any]:
        """Finalise onboarding: save profile, mark config, clear resume state."""

        if self._completed:
            return self.summary()
        self._completed = True
        try:
            self.profile_manager.save()
        except Exception:
            pass
        self._mark_config_complete()
        self.clear_state()
        return self.summary()

    def summary(self) -> Dict[str, Any]:
        """Return a warm summary of what was learned for confirmation."""

        profile = self.profile_manager.profile
        items = []
        try:
            items = profile.display_items()
        except Exception:
            items = []
        return {
            "message": self.conversation._completion_message(),
            "profile": self.profile_manager.to_dict(),
            "items": items,
            "fact_count": profile.known_fact_count(),
            "transcript": [
                {
                    "prompt": t.prompt,
                    "response": t.response,
                    "topic_id": t.topic_id,
                }
                for t in self.conversation.transcript
            ],
        }

    def apply_profile_edits(self, edited: Dict[str, Any]) -> None:
        """Replace the profile with user-edited data and persist it."""

        from mimosa.memory.profile_manager import UserProfile

        try:
            if "user_profile" not in edited:
                edited = {"user_profile": edited}
            self.profile_manager.profile = UserProfile.from_dict(edited)
            self.profile_manager.save()
        except Exception:
            pass

    # -- config helpers ---------------------------------------------------
    def _personality(self):
        if self.config_manager is None:
            return None
        try:
            return self.config_manager.get().personality
        except Exception:
            return None

    def _mark_config_complete(self) -> None:
        if self.config_manager is None:
            return
        try:
            self.config_manager.update_section(
                "personality", persist=True, onboarding_complete=True
            )
        except Exception:
            pass
