"""Skills package for MimOSA.

Skills are the concrete actions MimOSA can perform on the user's behalf. Each
skill exposes an async interface so the agent loop can route an interpreted
intent to the appropriate capability:

* **File operations** -- find, open, move, and organize files using the index.
* **Application launching** -- detect installed apps and launch the best tool
  for a task.
* **System control** -- volume, brightness, lock screen, screenshots, etc.
* **Research** -- multi-source web research with perspective synthesis.
* **Code generation** -- generate and test code in a sandboxed environment.

Future modules expected here: ``file_ops.py``, ``app_launcher.py``,
``research.py``, ``system_control.py``, and ``code_gen.py``.
"""
