"""Tests for the M2.3 KDEIntegration.

Hermetic across three transport scenarios:

1. **No transport** (no dbus-python, no qdbus) -- everything degrades to a clean
   ``available=False`` result. This mirrors the dev/CI box and GNOME hosts.
2. **qdbus subprocess** transport -- driven by a scripted fake runner.
3. **dbus-python** transport -- driven by a fake ``dbus`` module.

Run with:  pytest -q tests/test_kde_integration.py
"""

from __future__ import annotations

import pytest

from mimosa.system.kde_integration import KDEIntegration, RunOutput


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeShell:
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


# ---------------------------------------------------------------------------
# 1. No transport / non-KDE
# ---------------------------------------------------------------------------

class TestNoTransport:
    def _kde(self, is_kde=None):
        shell = FakeShell(available=())
        return KDEIntegration(dbus_module=None, runner=shell.run, which=shell.which, is_kde=is_kde)

    def test_not_available(self):
        kde = self._kde(is_kde=False)
        assert kde.available is False
        assert kde.has_transport is False

    def test_notification_degrades(self):
        kde = self._kde(is_kde=False)
        res = kde.send_notification("Title", "Body")
        assert res.available is False
        assert res.success is False
        assert "KDE" in res.message or "D-Bus" in res.message

    def test_virtual_desktops_degrade(self):
        res = self._kde(is_kde=False).get_virtual_desktops()
        assert res.available is False

    def test_kde_connect_degrades(self):
        res = self._kde(is_kde=False).get_kde_connect_devices()
        assert res.available is False

    def test_capabilities_report(self):
        caps = self._kde(is_kde=False).capabilities()
        assert caps["available"] is False
        assert caps["transport"] is None


# ---------------------------------------------------------------------------
# 2. qdbus subprocess transport
# ---------------------------------------------------------------------------

class TestQdbusTransport:
    def _kde(self, responses=None, available=("qdbus",), is_kde=True):
        shell = FakeShell(responses=responses or {}, available=available)
        kde = KDEIntegration(dbus_module=None, runner=shell.run, which=shell.which, is_kde=is_kde)
        return kde, shell

    def test_transport_detected(self):
        kde, _ = self._kde()
        assert kde.has_transport is True
        assert kde.available is True

    def test_qdbus6_preferred(self):
        kde, _ = self._kde(available=("qdbus6", "qdbus"))
        assert kde.capabilities()["transport"] == "qdbus6"

    def test_send_notification_success(self):
        kde, shell = self._kde(responses={"Notify": RunOutput(0, "12\n")})
        res = kde.send_notification("Hi", "There")
        assert res.success is True
        assert res.available is True
        # The Notify method must have been invoked.
        assert any("Notify" in " ".join(c) for c in shell.calls)

    def test_virtual_desktops_count(self):
        kde, _ = self._kde(responses={"count": RunOutput(0, "4\n")})
        res = kde.get_virtual_desktops()
        assert res.success is True
        assert res.data["count"] == 4

    def test_kde_connect_devices(self):
        kde, _ = self._kde(responses={"devices": RunOutput(0, "phone_id\ntablet_id\n")})
        res = kde.get_kde_connect_devices()
        assert res.success is True
        assert res.data["devices"] == ["phone_id", "tablet_id"]

    def test_kde_connect_no_devices(self):
        kde, _ = self._kde(responses={"devices": RunOutput(0, "\n")})
        res = kde.get_kde_connect_devices()
        assert res.success is True
        assert res.data["devices"] == []

    def test_non_kde_session_blocks_kwin_features(self):
        # qdbus present but the session is explicitly not KDE.
        kde, _ = self._kde(is_kde=False)
        res = kde.get_virtual_desktops()
        assert res.available is False


# ---------------------------------------------------------------------------
# 3. dbus-python transport
# ---------------------------------------------------------------------------

class FakeDBusInterface:
    def __init__(self, devices=None, props=None):
        self._devices = devices or []
        self._props = props or {}

    def Notify(self, *args):
        return 7

    def devices(self, reachable, paired):
        return list(self._devices)

    def Get(self, iface, prop):
        return self._props.get(prop, 0)


class FakeProxy:
    def __init__(self, iface):
        self._iface = iface

    def get_dbus_method(self, name, iface):
        return getattr(self._iface, name)


class FakeBus:
    def __init__(self, iface):
        self._iface = iface

    def get_object(self, service, path):
        return FakeProxy(self._iface)


class FakeDBusModule:
    def __init__(self, iface):
        self._iface = iface

    def SessionBus(self):
        return FakeBus(self._iface)

    def Interface(self, proxy, iface_name):
        return proxy._iface


class TestDBusPythonTransport:
    def test_notification_native(self):
        iface = FakeDBusInterface()
        kde = KDEIntegration(dbus_module=FakeDBusModule(iface), is_kde=True)
        res = kde.send_notification("Native", "Path")
        assert res.success is True
        assert kde.capabilities()["transport"] == "dbus-python"

    def test_virtual_desktops_native(self):
        iface = FakeDBusInterface(props={"count": 6})
        kde = KDEIntegration(dbus_module=FakeDBusModule(iface), is_kde=True)
        res = kde.get_virtual_desktops()
        assert res.success is True
        assert res.data["count"] == 6
