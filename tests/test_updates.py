"""Tests for the check-for-updates module (M4.2).

Fully offline: the network fetch is always injected as a fake callable, so no
HTTP request is ever made. Covers version parsing/comparison and the
:class:`UpdateChecker` happy/edge/error paths.
"""

from __future__ import annotations

import pytest

from mimosa.utils.updates import (
    UpdateChecker,
    UpdateInfo,
    compare_versions,
    is_newer,
    parse_version,
)


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected_xyz", [
    ("1.2.3", (1, 2, 3)),
    ("v1.2.3", (1, 2, 3)),
    ("2", (2, 0, 0)),
    ("2.5", (2, 5, 0)),
    ("  v0.1.0  ", (0, 1, 0)),
    ("1.0.0-alpha", (1, 0, 0)),
    ("1.0.0+build7", (1, 0, 0)),
])
def test_parse_version_ok(raw, expected_xyz):
    parsed = parse_version(raw)
    assert parsed is not None
    assert parsed[:3] == expected_xyz


@pytest.mark.parametrize("raw", ["", "abc", None, "v", "1.2.x"])
def test_parse_version_bad(raw):
    assert parse_version(raw) is None


# ---------------------------------------------------------------------------
# compare_versions / is_newer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b,expected", [
    ("1.0.0", "1.0.1", -1),
    ("1.1.0", "1.0.9", 1),
    ("2.0.0", "2.0.0", 0),
    ("v1.2.3", "1.2.3", 0),
    ("1.0", "1.0.0", 0),
    ("2", "1.9.9", 1),
])
def test_compare_versions(a, b, expected):
    assert compare_versions(a, b) == expected


def test_prerelease_sorts_before_final():
    # SemVer: 1.0.0-alpha < 1.0.0
    assert compare_versions("1.0.0-alpha", "1.0.0") == -1
    assert is_newer("1.0.0", "1.0.0-alpha")


def test_prerelease_numeric_ordering():
    assert compare_versions("1.0.0-rc.2", "1.0.0-rc.10") == -1


def test_compare_unparseable_treated_as_zero():
    assert compare_versions("garbage", "0.0.0") == 0
    assert compare_versions("1.0.0", "garbage") == 1


def test_is_newer():
    assert is_newer("0.2.0", "0.1.0")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("0.1.0", "0.2.0")


# ---------------------------------------------------------------------------
# UpdateChecker
# ---------------------------------------------------------------------------

def _fetcher(payload):
    def _f(url, timeout):
        _f.called_with = (url, timeout)
        return payload
    return _f


def test_checker_detects_available_update():
    chk = UpdateChecker(current_version="0.1.0",
                        fetcher=_fetcher({"tag_name": "v0.2.0",
                                          "html_url": "https://x/rel",
                                          "body": "notes"}))
    info = chk.check()
    assert isinstance(info, UpdateInfo)
    assert info.ok
    assert info.update_available
    assert info.latest_version == "v0.2.0"
    assert info.url == "https://x/rel"
    assert info.notes == "notes"
    assert "available" in info.summary()


def test_checker_up_to_date():
    chk = UpdateChecker(current_version="1.0.0", fetcher=_fetcher({"tag_name": "1.0.0"}))
    info = chk.check()
    assert info.ok
    assert not info.update_available
    assert "up to date" in info.summary()


def test_checker_accepts_plain_string_payload():
    chk = UpdateChecker(current_version="1.0.0", fetcher=_fetcher("1.5.0"))
    info = chk.check()
    assert info.update_available
    assert info.latest_version == "1.5.0"


def test_checker_handles_fetch_error():
    def boom(url, timeout):
        raise OSError("network down")
    chk = UpdateChecker(current_version="1.0.0", fetcher=boom)
    info = chk.check()
    assert not info.ok
    assert info.error is not None
    assert "network down" in info.summary()
    assert not info.update_available


def test_checker_no_release_info():
    chk = UpdateChecker(current_version="1.0.0", fetcher=_fetcher({}))
    info = chk.check()
    assert not info.ok
    assert "no release" in (info.error or "")


def test_checker_passes_url_and_timeout():
    f = _fetcher("1.0.0")
    chk = UpdateChecker(current_version="1.0.0", fetcher=f, url="https://api/x", timeout=2.0)
    chk.check()
    assert f.called_with == ("https://api/x", 2.0)


def test_checker_defaults_current_version_to_package():
    import mimosa
    chk = UpdateChecker(fetcher=_fetcher("0.0.1"))
    assert chk.current_version == mimosa.__version__


# ---------------------------------------------------------------------------
# SettingsController.check_for_updates (About page integration)
# ---------------------------------------------------------------------------

def test_settings_controller_check_for_updates_injectable():
    from mimosa.ui.settings_logic import SettingsController
    from mimosa.utils.config import AppConfigManager

    ctrl = SettingsController(AppConfigManager())
    checker = UpdateChecker(current_version="0.1.0", fetcher=_fetcher("0.9.0"))
    info = ctrl.check_for_updates(checker=checker)
    assert info.update_available
    assert info.latest_version == "0.9.0"
