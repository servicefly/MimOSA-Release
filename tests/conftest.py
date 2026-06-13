"""Shared pytest fixtures for the MimOSA test-suite.

The whole suite is designed to be fully offline / hermetic — no GTK display,
no audio devices, no network and no machine-learning back-ends are touched.

This module also guarantees test isolation for on-disk configuration.  Several
code-paths (most notably the headless first-run setup wizard) persist state to
the user configuration directory resolved from the environment.  Without
isolation those writes would pollute the developer's real ``~/.config/mimosa``
directory.  The autouse fixture below redirects configuration to a per-test
temporary directory by pointing ``XDG_CONFIG_HOME`` at it and clearing the
explicit ``MIMOSA_CONFIG`` / ``MIMOSA_UI_CONFIG`` overrides.

Tests that exercise path-resolution explicitly (e.g. ``MIMOSA_CONFIG`` env
override) simply re-set the relevant variables with their own ``monkeypatch``
calls; because this fixture is function-scoped and applied first, those local
overrides win for the duration of the test.
"""

from __future__ import annotations

import io
import struct
import wave

import pytest


def make_wav_bytes(num_samples: int = 1600, *, sample_rate: int = 16000,
                   amplitude: int = 1000) -> bytes:
    """Return a tiny valid 16 kHz mono 16-bit WAV blob for stub synthesis.

    Used by the Milestone-2 training tests so heavy TTS/ML back-ends never run.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack("<h", amplitude) * num_samples)
    return buf.getvalue()


@pytest.fixture()
def wav_bytes_factory():
    """Expose :func:`make_wav_bytes` as a fixture for convenience."""
    return make_wav_bytes


@pytest.fixture(autouse=True)
def _isolate_user_config(monkeypatch, tmp_path_factory):
    """Redirect configuration storage to a temporary directory.

    Ensures no test ever writes to the real user configuration directory.  A
    dedicated factory-provided directory is used (rather than the per-test
    ``tmp_path``) so the redirected config never appears inside a sandbox a
    test might itself enumerate.
    """
    cfg_home = tmp_path_factory.mktemp("xdg_config")
    # Clear explicit overrides so resolution falls back to XDG_CONFIG_HOME.
    monkeypatch.delenv("MIMOSA_CONFIG", raising=False)
    monkeypatch.delenv("MIMOSA_UI_CONFIG", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    yield
