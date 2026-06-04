"""System integration package for MimOSA.

Provides awareness of the host Linux environment so MimOSA can adapt its
behavior to the user's specific machine:

* **Distro detection** -- parse ``/etc/os-release`` to identify the
  distribution, version, package manager, and desktop environment.
* **Resource monitoring** -- track CPU, RAM, and GPU usage (via ``psutil``) to
  warn about heavy tasks and throttle gracefully.
* **Application registry** -- discover installed applications by parsing
  ``.desktop`` files.

Future modules expected here: ``distro_detect.py``, ``resource_monitor.py``,
and ``app_registry.py``.
"""
