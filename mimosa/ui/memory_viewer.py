"""GTK4 memory viewer dialog (M4) -- a read-only window into MimOSA's memory.

Where :mod:`mimosa.ui.profile_viewer` lets the user *edit* the structured
profile, this module gives a friendly, transparent **overview** of everything
MimOSA has picked up: the profile summary, the behavioural patterns it has
noticed, how the relationship has grown, and the proactive questions it has
asked. Transparency is a privacy feature -- the user can always see (and, via
Settings, clear) what is remembered.

As with the other UI shells, GTK is guarded at class-definition time: on a
headless machine :data:`HAS_GTK` is ``False`` and :data:`MemoryViewer` is
``None``. The text-building helpers are pure and unit-testable without GTK.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

try:
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    HAS_GTK = True
except Exception:  # pragma: no cover - headless import path
    HAS_GTK = False


CloseCallback = Callable[[], None]
ClearCallback = Callable[[], None]


# ---------------------------------------------------------------------------
# Pure, GTK-free formatting helpers (unit-testable)
# ---------------------------------------------------------------------------


def format_profile_section(profile: Optional[Dict[str, Any]]) -> str:
    """Render the learned profile as a friendly multi-line string."""
    data = {}
    if isinstance(profile, dict):
        data = profile.get("user_profile", profile)
    if not isinstance(data, dict) or not data:
        return "I haven't learned much about you yet — we're just getting started."
    lines: List[str] = []

    def _add(label: str, value: Any) -> None:
        if not value:
            return
        if isinstance(value, (list, tuple)):
            items = [str(v).strip() for v in value if str(v).strip()]
            if items:
                lines.append(f"{label}: {', '.join(items)}")
        elif isinstance(value, dict):
            pairs = [f"{k} ({v})" if v else str(k) for k, v in value.items()]
            if pairs:
                lines.append(f"{label}: {', '.join(pairs)}")
        else:
            text = str(value).strip()
            if text:
                lines.append(f"{label}: {text}")

    _add("Name", data.get("name"))
    _add("Occupation", data.get("occupation"))
    _add("Skills", data.get("skills"))
    _add("Tools", data.get("tools"))
    _add("Interests", data.get("interests"))
    _add("Preferences", data.get("preferences"))
    _add("Schedule", data.get("schedule"))
    _add("People", data.get("relationships"))
    _add("Goals", data.get("goals"))
    return "\n".join(lines) if lines else (
        "I haven't learned much about you yet — we're just getting started."
    )


def format_patterns_section(patterns: Optional[Sequence[Any]]) -> str:
    """Render detected patterns (objects or dicts) as a bulleted string."""
    if not patterns:
        return "No clear habits noticed yet."
    lines: List[str] = []
    for pat in patterns:
        desc = getattr(pat, "description", None)
        conf = getattr(pat, "confidence", None)
        if desc is None and isinstance(pat, dict):
            desc = pat.get("description") or pat.get("pattern")
            conf = pat.get("confidence")
        if not desc:
            continue
        if isinstance(conf, (int, float)):
            lines.append(f"• {desc} (confidence {int(round(conf * 100))}%)")
        else:
            lines.append(f"• {desc}")
    return "\n".join(lines) if lines else "No clear habits noticed yet."


def format_relationship_section(relationship: Optional[Any]) -> str:
    """Render a relationship summary object/dict into a friendly string."""
    if relationship is None:
        return "We're just getting to know each other."
    if isinstance(relationship, str):
        return relationship
    stage = getattr(relationship, "stage", None)
    days = getattr(relationship, "days_known", None)
    convos = getattr(relationship, "conversations", None)
    if stage is None and isinstance(relationship, dict):
        stage = relationship.get("stage")
        days = relationship.get("days_known")
        convos = relationship.get("conversations")
    bits: List[str] = []
    if stage:
        nice = {
            "new": "We're still new friends",
            "familiar": "We're getting familiar",
            "close": "We've become close",
        }.get(str(stage), f"Stage: {stage}")
        bits.append(nice)
    if isinstance(days, int) and days >= 0:
        bits.append(f"{days} day(s) together")
    if isinstance(convos, int) and convos > 0:
        bits.append(f"{convos} conversation(s)")
    return " · ".join(bits) if bits else "We're just getting to know each other."


def format_questions_section(questions: Optional[Sequence[Any]]) -> str:
    """Render the proactive questions MimOSA has asked."""
    if not questions:
        return "I haven't needed to ask you anything yet."
    lines: List[str] = []
    for q in questions:
        text = q.get("question") if isinstance(q, dict) else getattr(q, "text", None)
        if text:
            lines.append(f"• {text}")
    return "\n".join(lines) if lines else "I haven't needed to ask you anything yet."


def build_memory_overview(
    *,
    profile: Optional[Dict[str, Any]] = None,
    patterns: Optional[Sequence[Any]] = None,
    relationship: Optional[Any] = None,
    questions: Optional[Sequence[Any]] = None,
) -> Dict[str, str]:
    """Build the full overview as ``{section_title: body}``. Pure & testable."""
    return {
        "What I know about you": format_profile_section(profile),
        "Habits I've noticed": format_patterns_section(patterns),
        "Our relationship": format_relationship_section(relationship),
        "Questions I've asked": format_questions_section(questions),
    }


if HAS_GTK:

    class MemoryViewer(Gtk.Window):  # pragma: no cover - exercised only under GTK
        """A scrollable, read-only window summarising MimOSA's memory."""

        def __init__(
            self,
            *,
            overview: Dict[str, str],
            transient_for=None,
            on_close: Optional[CloseCallback] = None,
            on_clear: Optional[ClearCallback] = None,
        ) -> None:
            super().__init__(title="What MimOSA Remembers")
            self._on_close = on_close
            self._on_clear = on_clear
            self.set_modal(True)
            self.set_default_size(560, 600)
            if transient_for is not None:
                self.set_transient_for(transient_for)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            root.set_margin_top(16)
            root.set_margin_bottom(16)
            root.set_margin_start(18)
            root.set_margin_end(18)
            self.set_child(root)

            intro = Gtk.Label(
                label="Everything here lives only on this device. You're always "
                      "in control — clear it any time.",
                xalign=0.0)
            intro.add_css_class("dim-label")
            intro.set_wrap(True)
            root.append(intro)

            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_vexpand(True)
            root.append(scroller)

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            scroller.set_child(content)
            for title, body in (overview or {}).items():
                heading = Gtk.Label(xalign=0.0)
                heading.set_markup(f"<b>{_escape(title)}</b>")
                content.append(heading)
                body_label = Gtk.Label(label=body, xalign=0.0)
                body_label.set_wrap(True)
                body_label.set_selectable(True)
                content.append(body_label)

            actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            actions.set_halign(Gtk.Align.END)
            actions.set_margin_top(8)
            if on_clear is not None:
                clear = Gtk.Button(label="Clear All Memories")
                clear.add_css_class("destructive-action")
                clear.connect("clicked", self._on_clear_clicked)
                actions.append(clear)
            close = Gtk.Button(label="Close")
            close.add_css_class("suggested-action")
            close.connect("clicked", self._on_close_clicked)
            actions.append(close)
            root.append(actions)

        def _on_clear_clicked(self, *_a) -> None:
            if self._on_clear is not None:
                try:
                    self._on_clear()
                except Exception:
                    logger.debug("clear hook failed", exc_info=True)
            self._shut()

        def _on_close_clicked(self, *_a) -> None:
            self._shut()

        def _shut(self) -> None:
            if self._on_close is not None:
                try:
                    self._on_close()
                except Exception:
                    logger.debug("close hook failed", exc_info=True)
            try:
                self.close()
            except Exception:
                pass

else:  # pragma: no cover - headless
    MemoryViewer = None  # type: ignore


def _escape(text: str) -> str:
    """Minimal markup escaping for Pango labels."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def open_memory_viewer(
    *,
    overview: Optional[Dict[str, str]] = None,
    profile: Optional[Dict[str, Any]] = None,
    patterns: Optional[Sequence[Any]] = None,
    relationship: Optional[Any] = None,
    questions: Optional[Sequence[Any]] = None,
    transient_for=None,
    on_close: Optional[CloseCallback] = None,
    on_clear: Optional[ClearCallback] = None,
):
    """Construct, show, and return a :class:`MemoryViewer`.

    Returns ``None`` on a headless machine (after invoking ``on_close``) so
    callers can invoke it unconditionally. If ``overview`` is not supplied it is
    built from the individual pieces via :func:`build_memory_overview`.
    """
    if overview is None:
        overview = build_memory_overview(
            profile=profile,
            patterns=patterns,
            relationship=relationship,
            questions=questions,
        )
    if not HAS_GTK or MemoryViewer is None:
        logger.warning("Memory viewer requested but GTK is unavailable")
        if on_close is not None:
            try:
                on_close()
            except Exception:  # pragma: no cover - best-effort
                logger.debug("close hook failed", exc_info=True)
        return None
    viewer = MemoryViewer(
        overview=overview,
        transient_for=transient_for,
        on_close=on_close,
        on_clear=on_clear,
    )
    viewer.present()
    return viewer
