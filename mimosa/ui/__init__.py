"""User interface package for MimOSA (GTK4 -- Phase 3).

Houses the graphical interface that gives MimOSA a visible, personality-driven
presence on the desktop:

* **System tray icon** -- status indicator (idle, listening, thinking,
  speaking).
* **Chat window** -- a minimal, expandable conversation view with a text input
  field as a backup to voice.
* **2D avatar** -- a personality-driven avatar with sprite-based animation
  (mouth sync, expressions).

The UI is built with GTK4 (Cairo/Pixbuf for avatar rendering) for native,
lightweight Linux integration. These modules are scheduled for Phase 3 and are
intentionally left as placeholders during M1.1.

Future modules expected here: ``tray.py``, ``chat_window.py``, and
``avatar.py``.
"""
