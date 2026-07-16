"""Question bank for MimOSA's conversational onboarding (M3).

The onboarding experience walks through seven *topics*.  Each topic has a
primary opening question plus a set of *adaptive follow-ups* that MimOSA can
ask when an answer is thin/vague or when it simply wants to dig a little
deeper — exactly how a curious friend would.  Questions are deliberately
open-ended and conversational rather than form-like.

Each :class:`Question` also carries ``profile_fields`` — a hint to the fact
extractor about which parts of the user profile this question is most likely
to populate (``skills``, ``tools``, ``interests`` …).  These hints make the
heuristic fallback extractor far more reliable when no LLM is available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

__all__ = [
    "Question",
    "Topic",
    "QUESTION_BANK",
    "all_topics",
    "get_topic",
    "total_topics",
]


@dataclass(frozen=True)
class Question:
    """A single onboarding prompt with optional adaptive follow-ups."""

    id: str
    text: str
    #: Warm, casual follow-ups asked to draw out more detail.
    follow_ups: Tuple[str, ...] = ()
    #: Profile fields this question most likely informs (extraction hint).
    profile_fields: Tuple[str, ...] = ()
    #: If True the engine may skip straight on when the user gives a rich
    #: answer; if False it is treated as essential context.
    optional: bool = False


@dataclass(frozen=True)
class Topic:
    """A themed group of questions within the onboarding conversation."""

    id: str
    title: str
    #: A short friendly lead-in shown/spoken when the topic begins.
    intro: str
    questions: Tuple[Question, ...] = ()

    @property
    def primary(self) -> Optional[Question]:
        return self.questions[0] if self.questions else None


# ---------------------------------------------------------------------------
# The seven onboarding topics.
# ---------------------------------------------------------------------------

QUESTION_BANK: Tuple[Topic, ...] = (
    Topic(
        id="introduction",
        title="Getting to know you",
        intro=(
            "I'd love to get to know you a little before we dive in. "
            "No wrong answers here — just chatting!"
        ),
        questions=(
            Question(
                id="intro_name",
                text="So, what should I call you?",
                follow_ups=(
                    "Nice to meet you! Is there a nickname you prefer, or is "
                    "that the one?",
                ),
                profile_fields=("name",),
            ),
            Question(
                id="intro_about",
                text=(
                    "Tell me a bit about yourself — whatever comes to mind. "
                    "e.g. your name, what you do for work, things you enjoy — "
                    "whatever feels relevant."
                ),
                follow_ups=(
                    "Oh, I'd love to hear more about that!",
                    "That's interesting — what else should I know about you?",
                ),
                profile_fields=("name", "occupation", "interests"),
            ),
        ),
    ),
    Topic(
        id="professional_life",
        title="Work & what you do",
        intro="Let's talk about what keeps you busy day to day.",
        questions=(
            Question(
                id="work_role",
                text="What do you do for work — or what are you studying?",
                follow_ups=(
                    "Nice! How long have you been doing that?",
                    "What's your favourite part of it?",
                ),
                profile_fields=("occupation",),
            ),
            Question(
                id="work_skills",
                text=(
                    "What kind of skills or tools do you use most in your work?"
                ),
                follow_ups=(
                    "Are there any you're hoping to get better at?",
                ),
                profile_fields=("skills", "tools"),
            ),
        ),
    ),
    Topic(
        id="interests_hobbies",
        title="Interests & hobbies",
        intro="Now for the fun stuff — what do you love doing?",
        questions=(
            Question(
                id="hobbies_main",
                text="What do you like to do when you're not working?",
                follow_ups=(
                    "That sounds great — how did you get into it?",
                    "Anything else you're into?",
                ),
                profile_fields=("interests",),
            ),
            Question(
                id="hobbies_passion",
                text="Is there something you could talk about for hours?",
                follow_ups=(
                    "I love that kind of passion — tell me more!",
                ),
                profile_fields=("interests", "goals"),
                optional=True,
            ),
        ),
    ),
    Topic(
        id="lifestyle_preferences",
        title="Your daily rhythm",
        intro="A few quick things about how your days usually go.",
        questions=(
            Question(
                id="lifestyle_rhythm",
                text=(
                    "Are you more of an early bird or a night owl?"
                ),
                follow_ups=(
                    "Good to know! When do you usually get your best work done?",
                ),
                profile_fields=("schedule", "preferences"),
            ),
            Question(
                id="lifestyle_style",
                text=(
                    "When you're tackling something, do you like lots of detail "
                    "or just the quick version?"
                ),
                follow_ups=(
                    "Got it — I'll keep that in mind for how I talk with you.",
                ),
                profile_fields=("preferences",),
            ),
        ),
    ),
    Topic(
        id="system_usage",
        title="How you use your computer",
        intro=(
            "Since I'll be helping out on your machine, tell me how you like "
            "to work."
        ),
        questions=(
            Question(
                id="system_tools",
                text=(
                    "What apps or tools do you spend the most time in?"
                ),
                follow_ups=(
                    "Anything you wish worked a little better for you?",
                ),
                profile_fields=("tools",),
            ),
            Question(
                id="system_help",
                text=(
                    "What kinds of things would you most like a hand with?"
                ),
                follow_ups=(
                    "Great — that helps me know where to jump in.",
                ),
                profile_fields=("goals", "preferences"),
            ),
        ),
    ),
    Topic(
        id="assistance_style",
        title="How I can help best",
        intro="Let's make sure I show up the way you'd like me to.",
        questions=(
            Question(
                id="assist_tone",
                text=(
                    "Do you prefer I keep things short and to the point, or "
                    "chat a bit more?"
                ),
                follow_ups=(
                    "Perfect — I'll match that.",
                ),
                profile_fields=("preferences",),
            ),
            Question(
                id="assist_proactive",
                text=(
                    "Should I speak up with suggestions, or wait until you ask?"
                ),
                follow_ups=(
                    "Noted — thanks for letting me know.",
                ),
                profile_fields=("preferences",),
            ),
        ),
    ),
    Topic(
        id="relationships_goals",
        title="People & goals",
        intro="Last one — a little about what matters to you.",
        questions=(
            Question(
                id="goals_main",
                text="Is there anything you're working toward right now?",
                follow_ups=(
                    "That's a great goal — anything I can do to help you get "
                    "there?",
                ),
                profile_fields=("goals",),
            ),
            Question(
                id="relationships_people",
                text=(
                    "Are there important people in your life I should know "
                    "about — so I can help you remember names, dates, that "
                    "sort of thing?"
                ),
                follow_ups=(
                    "Thanks for sharing — I'll keep that close.",
                ),
                profile_fields=("relationships",),
                optional=True,
            ),
        ),
    ),
)


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------

def all_topics() -> Tuple[Topic, ...]:
    """Return the ordered tuple of onboarding topics."""

    return QUESTION_BANK


def total_topics() -> int:
    """Return the number of onboarding topics (7)."""

    return len(QUESTION_BANK)


def get_topic(topic_id: str) -> Optional[Topic]:
    """Return the topic with *topic_id*, or ``None`` if not found."""

    for topic in QUESTION_BANK:
        if topic.id == topic_id:
            return topic
    return None
