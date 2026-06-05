"""Tests for the M2.3 SystemProfiler.

Fully hermetic: the os-release path is redirected to a pytest ``tmp_path`` file,
the environment is an explicit dict, and the ``plasmashell --version`` probe is
served by a fake runner. No test depends on the host the suite runs on (which
is deliberately *not* Kubuntu/KDE on the dev/CI box).

Run with:  pytest -q tests/test_system_profiler.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mimosa.system.system_profiler import RunOutput, SystemProfiler


KUBUNTU_OS_RELEASE = '''\
NAME="Ubuntu"
VERSION="26.04 LTS (Resolute Raccoon)"
ID=ubuntu
ID_LIKE=debian
PRETTY_NAME="Ubuntu 26.04 LTS"
VERSION_ID="26.04"
VERSION_CODENAME=resolute
'''

FEDORA_OS_RELEASE = '''\
NAME="Fedora Linux"
VERSION="40 (KDE Plasma)"
ID=fedora
PRETTY_NAME="Fedora Linux 40 (KDE Plasma)"
VERSION_ID=40
'''


def write_os_release(tmp_path: Path, content: str) -> str:
    p = tmp_path / "os-release"
    p.write_text(content)
    return str(p)


def make_profiler(tmp_path, content=KUBUNTU_OS_RELEASE, env=None, plasma=None, available=()):
    path = write_os_release(tmp_path, content)

    def which(tool):
        return f"/usr/bin/{tool}" if tool in available else None

    def runner(argv):
        if argv and argv[0] == "plasmashell" and plasma is not None:
            return RunOutput(0, f"plasmashell {plasma}\n")
        return RunOutput(127, "", "not found")

    return SystemProfiler(
        os_release_path=path,
        environ=env or {},
        runner=runner,
        which=which,
    )


class TestOSRelease:
    def test_parses_kubuntu(self, tmp_path):
        prof = make_profiler(tmp_path).profile
        assert prof.distro_id == "ubuntu"
        assert prof.distro_version == "26.04"
        assert prof.distro_name == "Ubuntu 26.04 LTS"

    def test_missing_os_release_is_graceful(self, tmp_path):
        p = SystemProfiler(os_release_path=str(tmp_path / "nope"), environ={})
        prof = p.profile
        assert prof.distro_id is None
        assert prof.distro_name is None

    def test_quoted_values_unquoted(self, tmp_path):
        prof = make_profiler(tmp_path).profile
        # VERSION had surrounding quotes that must be stripped.
        assert prof.raw_os_release["VERSION"].startswith("26.04 LTS")


class TestDesktopDetection:
    def test_kde_session(self, tmp_path):
        env = {"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"}
        prof = make_profiler(tmp_path, env=env).profile
        assert prof.desktop_environment == "KDE"
        assert prof.is_kde is True
        assert prof.is_kubuntu is True  # ubuntu + KDE session

    def test_gnome_colon_prefixed(self, tmp_path):
        env = {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
        prof = make_profiler(tmp_path, env=env).profile
        assert prof.desktop_environment == "GNOME"
        assert prof.is_kde is False

    def test_fallback_to_desktop_session(self, tmp_path):
        env = {"DESKTOP_SESSION": "plasma"}
        prof = make_profiler(tmp_path, env=env).profile
        assert prof.desktop_environment == "KDE"

    def test_unknown_desktop_preserved(self, tmp_path):
        env = {"XDG_CURRENT_DESKTOP": "MyWeirdWM"}
        prof = make_profiler(tmp_path, env=env).profile
        assert prof.desktop_environment is not None
        assert prof.is_kde is False

    def test_no_desktop_env(self, tmp_path):
        prof = make_profiler(tmp_path, env={}).profile
        assert prof.desktop_environment is None


class TestDisplayServer:
    def test_explicit_wayland(self, tmp_path):
        prof = make_profiler(tmp_path, env={"XDG_SESSION_TYPE": "wayland"}).profile
        assert prof.display_server == "wayland"

    def test_explicit_x11(self, tmp_path):
        prof = make_profiler(tmp_path, env={"XDG_SESSION_TYPE": "x11"}).profile
        assert prof.display_server == "x11"

    def test_infer_wayland_from_socket(self, tmp_path):
        prof = make_profiler(tmp_path, env={"WAYLAND_DISPLAY": "wayland-0"}).profile
        assert prof.display_server == "wayland"

    def test_infer_x11_from_display(self, tmp_path):
        prof = make_profiler(tmp_path, env={"DISPLAY": ":0"}).profile
        assert prof.display_server == "x11"

    def test_tty_has_no_display_server(self, tmp_path):
        prof = make_profiler(tmp_path, env={"XDG_SESSION_TYPE": "tty"}).profile
        assert prof.display_server is None


class TestPlasmaVersion:
    def test_from_plasmashell(self, tmp_path):
        env = {"XDG_CURRENT_DESKTOP": "KDE"}
        prof = make_profiler(tmp_path, env=env, plasma="6.0.4", available=("plasmashell",)).profile
        assert prof.plasma_version == "6.0.4"

    def test_from_env_var_when_no_binary(self, tmp_path):
        env = {"XDG_CURRENT_DESKTOP": "KDE", "KDE_SESSION_VERSION": "6"}
        prof = make_profiler(tmp_path, env=env).profile
        assert prof.plasma_version == "6"

    def test_no_plasma_when_not_kde(self, tmp_path):
        env = {"XDG_CURRENT_DESKTOP": "GNOME"}
        prof = make_profiler(tmp_path, env=env, plasma="6.0", available=("plasmashell",)).profile
        assert prof.plasma_version is None


class TestKubuntuFlag:
    def test_fedora_kde_is_not_kubuntu(self, tmp_path):
        env = {"XDG_CURRENT_DESKTOP": "KDE"}
        prof = make_profiler(tmp_path, content=FEDORA_OS_RELEASE, env=env).profile
        assert prof.is_kde is True
        assert prof.is_kubuntu is False  # not ubuntu base

    def test_ubuntu_gnome_is_not_kubuntu(self, tmp_path):
        env = {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}
        prof = make_profiler(tmp_path, env=env).profile
        assert prof.is_kubuntu is False


class TestCachingAndSummary:
    def test_profile_is_cached(self, tmp_path):
        p = make_profiler(tmp_path)
        first = p.profile
        assert p.profile is first  # same object, memoized

    def test_refresh_recomputes(self, tmp_path):
        p = make_profiler(tmp_path)
        first = p.profile
        second = p.refresh()
        assert second is not first

    def test_summary_mentions_distro(self, tmp_path):
        env = {"XDG_CURRENT_DESKTOP": "KDE", "XDG_SESSION_TYPE": "wayland"}
        summary = make_profiler(tmp_path, env=env).profile.summary()
        assert "Ubuntu" in summary
        assert "KDE" in summary

    def test_platform_fields_present(self, tmp_path):
        prof = make_profiler(tmp_path).profile
        assert prof.architecture  # platform.machine() always returns something
        assert prof.python_version
