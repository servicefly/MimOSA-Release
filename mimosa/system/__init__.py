"""System integration package for MimOSA.

Provides awareness of the host Linux environment so MimOSA can adapt its
behavior to the user's specific machine:

* **System profiling** (``system_profiler.py``) -- parse ``/etc/os-release``
  and the XDG session variables to identify the distribution, version, desktop
  environment (KDE/GNOME/...), display server (Wayland/X11), KDE Plasma version,
  architecture, and kernel.
* **Hardware detection** (``hardware_detector.py``) -- the audio backend
  (PipeWire/PulseAudio/ALSA), displays, microphones, CPU, RAM, and GPU via
  ``psutil``, ``/proc``, ``/sys`` and standard CLI probes.
* **KDE integration** (``kde_integration.py``) -- KDE Plasma features over
  D-Bus (notifications, virtual desktops, KDE Connect), with a non-KDE-safe
  fallback.
* **System optimization** (``system_optimizer.py``) -- derive hardware-aware
  runtime settings (audio backend, wake-word sensitivity, STT model size, TTS
  quality, conversation-history limit).
* **System control** (``system_control.py``) -- volume/brightness/Wi-Fi/battery.
* **Application registry** (``app_registry.py``) -- discover installed
  applications by parsing ``.desktop`` files.

All detection is local, cached, non-blocking, and degrades gracefully on
non-Kubuntu hosts so the package is fully testable off-target.
"""
