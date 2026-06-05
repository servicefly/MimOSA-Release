"""Tests for the M2.2 SystemController and SystemControlSkill.

Fully hermetic: the controller's subprocess ``runner`` and tool-discovery
``which`` are injected fakes, and the battery sysfs root is redirected to a
pytest ``tmp_path``. No real system state (audio, brightness, Wi-Fi) is touched
and no external binaries are required -- which matters because the CI/dev box
has none of amixer/pactl/wpctl/brightnessctl/nmcli installed.

Run with:  pytest -q tests/test_system_control.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mimosa.system.system_control import (
    CommandResult,
    RunOutput,
    SystemController,
)
from mimosa.skills.system_control import SystemControlSkill
from mimosa.core.intent_router import IntentRouter, INTENT_SYSTEM


# ---------------------------------------------------------------------------
# Fake runner infrastructure
# ---------------------------------------------------------------------------

class FakeShell:
    """Records argv calls and returns scripted :class:`RunOutput` responses.

    ``responses`` maps a substring of the joined command to a RunOutput. The
    first matching key wins; unmatched commands return success with empty
    output.
    """

    def __init__(self, responses=None, available=()):
        self.responses = responses or {}
        self.available = set(available)
        self.calls = []

    def run(self, argv):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for key, out in self.responses.items():
            if key in joined:
                return out
        return RunOutput(0, "", "")

    def which(self, tool):
        return f"/usr/bin/{tool}" if tool in self.available else None


def make_controller(responses=None, available=(), power_root=None):
    shell = FakeShell(responses=responses, available=available)
    controller = SystemController(
        runner=shell.run,
        which=shell.which,
        power_supply_root=str(power_root) if power_root else "/nonexistent",
    )
    return controller, shell


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

class TestVolume:
    def test_no_backend_degrades_gracefully(self):
        ctl, _ = make_controller(available=())
        res = ctl.set_volume(50)
        assert not res.success
        assert "no supported tool" in res.message

    def test_set_volume_pactl(self):
        ctl, shell = make_controller(available=("pactl",))
        res = ctl.set_volume(40)
        assert res.success and res.data["volume"] == 40
        assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "40%"] in shell.calls

    def test_set_volume_clamped(self):
        ctl, _ = make_controller(available=("pactl",))
        assert ctl.set_volume(250).data["volume"] == 100
        assert ctl.set_volume(-5).data["volume"] == 0

    def test_get_volume_wpctl(self):
        ctl, _ = make_controller(
            responses={"get-volume": RunOutput(0, "Volume: 0.65\n")},
            available=("wpctl",),
        )
        res = ctl.get_volume()
        assert res.success and res.data["volume"] == 65 and res.data["muted"] is False

    def test_get_volume_wpctl_muted(self):
        ctl, _ = make_controller(
            responses={"get-volume": RunOutput(0, "Volume: 0.30 [MUTED]\n")},
            available=("wpctl",),
        )
        res = ctl.get_volume()
        assert res.data["volume"] == 30 and res.data["muted"] is True

    def test_get_volume_amixer(self):
        ctl, _ = make_controller(
            responses={"get": RunOutput(0, "  Front Left: Playback [75%] [on]\n")},
            available=("amixer",),
        )
        res = ctl.get_volume()
        assert res.success and res.data["volume"] == 75

    def test_change_volume_up_reports_new_level(self):
        ctl, shell = make_controller(
            responses={"get-sink-volume": RunOutput(0, "Volume: ... 55% ..."),
                       "get-sink-mute": RunOutput(0, "Mute: no")},
            available=("pactl",),
        )
        res = ctl.change_volume(10)
        assert res.success and res.data["volume"] == 55
        assert any("+10%" in " ".join(c) for c in shell.calls)

    def test_mute_amixer(self):
        ctl, shell = make_controller(available=("amixer",))
        res = ctl.set_mute(True)
        assert res.success and res.data["muted"] is True
        assert ["amixer", "set", "Master", "mute"] in shell.calls

    def test_command_failure(self):
        ctl, _ = make_controller(
            responses={"set-sink-volume": RunOutput(1, "", "boom")},
            available=("pactl",),
        )
        res = ctl.set_volume(20)
        assert not res.success and "didn't work" in res.message


# ---------------------------------------------------------------------------
# Brightness
# ---------------------------------------------------------------------------

class TestBrightness:
    def test_no_backend(self):
        ctl, _ = make_controller(available=())
        assert not ctl.set_brightness(50).success

    def test_set_brightness_brightnessctl(self):
        ctl, shell = make_controller(available=("brightnessctl",))
        res = ctl.set_brightness(60)
        assert res.success and res.data["brightness"] == 60
        assert ["brightnessctl", "set", "60%"] in shell.calls

    def test_get_brightness_brightnessctl(self):
        ctl, _ = make_controller(
            responses={"brightnessctl get": RunOutput(0, "120\n"),
                       "brightnessctl max": RunOutput(0, "240\n")},
            available=("brightnessctl",),
        )
        res = ctl.get_brightness()
        assert res.success and res.data["brightness"] == 50

    def test_change_brightness_down(self):
        ctl, shell = make_controller(
            responses={"get": RunOutput(0, "100"), "max": RunOutput(0, "200")},
            available=("brightnessctl",),
        )
        res = ctl.change_brightness(-10)
        assert res.success
        assert any("10%-" in " ".join(c) for c in shell.calls)

    def test_set_brightness_xbacklight(self):
        ctl, shell = make_controller(available=("xbacklight",))
        res = ctl.set_brightness(45)
        assert res.success
        assert ["xbacklight", "-set", "45"] in shell.calls


# ---------------------------------------------------------------------------
# Wi-Fi
# ---------------------------------------------------------------------------

class TestWifi:
    def test_no_nmcli(self):
        ctl, _ = make_controller(available=())
        assert not ctl.get_wifi_status().success

    def test_status_enabled_connected(self):
        ctl, _ = make_controller(
            responses={"radio wifi": RunOutput(0, "enabled\n"),
                       "dev wifi": RunOutput(0, "yes:HomeNet\nno:OtherNet\n")},
            available=("nmcli",),
        )
        res = ctl.get_wifi_status()
        assert res.success and res.data["enabled"] is True
        assert res.data["ssid"] == "HomeNet"

    def test_status_disabled(self):
        ctl, _ = make_controller(
            responses={"radio wifi": RunOutput(0, "disabled\n")},
            available=("nmcli",),
        )
        res = ctl.get_wifi_status()
        assert res.data["enabled"] is False
        assert "off" in res.message

    def test_set_wifi_on(self):
        ctl, shell = make_controller(available=("nmcli",))
        res = ctl.set_wifi(True)
        assert res.success and res.data["enabled"] is True
        assert ["nmcli", "radio", "wifi", "on"] in shell.calls


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------

class TestBattery:
    def test_no_battery_dir(self):
        ctl, _ = make_controller(power_root="/nonexistent")
        res = ctl.get_battery()
        assert not res.success and res.data["present"] is False

    def test_no_battery_present(self, tmp_path):
        (tmp_path / "AC").mkdir()
        ctl, _ = make_controller(power_root=tmp_path)
        res = ctl.get_battery()
        assert not res.success
        assert "doesn't appear to have a battery" in res.message

    def test_battery_discharging(self, tmp_path):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("72\n")
        (bat / "status").write_text("Discharging\n")
        ctl, _ = make_controller(power_root=tmp_path)
        res = ctl.get_battery()
        assert res.success and res.data["percent"] == 72
        assert res.data["charging"] is False
        assert "72 percent" in res.message

    def test_battery_charging(self, tmp_path):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("40")
        (bat / "status").write_text("Charging")
        ctl, _ = make_controller(power_root=tmp_path)
        res = ctl.get_battery()
        assert res.data["charging"] is True and "charging" in res.message

    def test_battery_full(self, tmp_path):
        bat = tmp_path / "BAT1"
        bat.mkdir()
        (bat / "capacity").write_text("100")
        (bat / "status").write_text("Full")
        ctl, _ = make_controller(power_root=tmp_path)
        res = ctl.get_battery()
        assert res.success and "fully charged" in res.message


# ---------------------------------------------------------------------------
# Skill: NL parsing
# ---------------------------------------------------------------------------

@pytest.fixture
def vol_skill():
    ctl, shell = make_controller(
        responses={"get-sink-volume": RunOutput(0, "Volume: 50%"),
                   "get-sink-mute": RunOutput(0, "Mute: no")},
        available=("pactl",),
    )
    return SystemControlSkill(controller=ctl), shell


class TestSkillVolume:
    def test_volume_up(self, vol_skill):
        skill, shell = vol_skill
        res = skill.handle("turn the volume up")
        assert res.success
        assert any("set-sink-volume" in " ".join(c) and "+" in " ".join(c)
                   for c in shell.calls)

    def test_volume_set_percent(self, vol_skill):
        skill, shell = vol_skill
        res = skill.handle("set volume to 30 percent")
        assert res.success
        assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "30%"] in shell.calls

    def test_mute(self, vol_skill):
        skill, shell = vol_skill
        res = skill.handle("mute")
        assert res.success
        assert any("set-sink-mute" in " ".join(c) for c in shell.calls)

    def test_unmute(self, vol_skill):
        skill, shell = vol_skill
        res = skill.handle("unmute the audio")
        assert res.success
        assert any(c[-1] == "0" for c in shell.calls if "set-sink-mute" in " ".join(c))


class TestSkillBrightness:
    def test_brightness_down(self):
        ctl, shell = make_controller(
            responses={"get": RunOutput(0, "100"), "max": RunOutput(0, "200")},
            available=("brightnessctl",),
        )
        skill = SystemControlSkill(controller=ctl)
        res = skill.handle("turn the brightness down")
        assert res.success
        assert any("%-" in " ".join(c) for c in shell.calls)

    def test_brightness_set(self):
        ctl, shell = make_controller(available=("brightnessctl",))
        skill = SystemControlSkill(controller=ctl)
        res = skill.handle("set brightness to 70 percent")
        assert res.success
        assert ["brightnessctl", "set", "70%"] in shell.calls

    def test_brightness_no_tool(self):
        ctl, _ = make_controller(available=())
        skill = SystemControlSkill(controller=ctl)
        res = skill.handle("brightness up")
        assert not res.success and "no supported tool" in res.text


class TestSkillWifi:
    def test_wifi_on(self):
        ctl, shell = make_controller(available=("nmcli",))
        skill = SystemControlSkill(controller=ctl)
        res = skill.handle("turn wifi on")
        assert res.success
        assert ["nmcli", "radio", "wifi", "on"] in shell.calls

    def test_wifi_off_requires_confirmation(self):
        ctl, shell = make_controller(available=("nmcli",))
        skill = SystemControlSkill(controller=ctl)
        res = skill.handle("turn wifi off")
        assert res.metadata["operation"] == "confirm_required"
        assert skill.has_pending_confirmation()
        # nmcli not called yet.
        assert not any("radio wifi off" in " ".join(c) for c in shell.calls)
        res2 = skill.handle("yes")
        assert res2.success
        assert ["nmcli", "radio", "wifi", "off"] in shell.calls

    def test_wifi_off_cancel(self):
        ctl, shell = make_controller(available=("nmcli",))
        skill = SystemControlSkill(controller=ctl)
        skill.handle("turn wifi off")
        res = skill.handle("no")
        assert "leave it" in res.text.lower()
        assert not any(c == ["nmcli", "radio", "wifi", "off"] for c in shell.calls)

    def test_wifi_status(self):
        ctl, _ = make_controller(
            responses={"radio wifi": RunOutput(0, "enabled"),
                       "dev wifi": RunOutput(0, "yes:CafeNet")},
            available=("nmcli",),
        )
        skill = SystemControlSkill(controller=ctl)
        res = skill.handle("is my wifi on?")
        assert res.success and "CafeNet" in res.text


class TestSkillBattery:
    def test_battery_query(self, tmp_path):
        bat = tmp_path / "BAT0"
        bat.mkdir()
        (bat / "capacity").write_text("88")
        (bat / "status").write_text("Discharging")
        ctl, _ = make_controller(power_root=tmp_path)
        skill = SystemControlSkill(controller=ctl)
        res = skill.handle("how much battery do I have left")
        assert res.success and "88 percent" in res.text


# ---------------------------------------------------------------------------
# Routing integration
# ---------------------------------------------------------------------------

class TestRouting:
    @pytest.mark.parametrize("text", [
        "turn the volume up", "set brightness to 40 percent", "mute",
        "is my wifi on", "how much battery do I have left",
    ])
    def test_router_classifies_system(self, text):
        router = IntentRouter()
        assert router.classify(text).intent == INTENT_SYSTEM

    def test_router_dispatches(self):
        ctl, shell = make_controller(available=("pactl",))
        skill = SystemControlSkill(controller=ctl)
        router = IntentRouter(skills=[skill])
        res = router.route("set volume to 25 percent")
        assert res.success
        assert ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "25%"] in shell.calls

    def test_router_pending_confirmation_routes_back(self):
        ctl, shell = make_controller(available=("nmcli",))
        skill = SystemControlSkill(controller=ctl)
        router = IntentRouter(skills=[skill])
        router.route("turn wifi off")
        router.route("yes")
        assert ["nmcli", "radio", "wifi", "off"] in shell.calls
