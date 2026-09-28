import html
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from aiida.engine.daemon.client import DaemonException
from aiida.manage.configuration import Profile, load_config

import home.control as control_module
from home.control import (
    AiidaStatusOverviewWidget,
    ControlSectionWidget,
    ProfileControlWidget,
    State,
    SystemResourcesWidget,
    _cpu_status,
    _DaemonClient,
    _format_bytes,
    _memory_status,
    _read_cgroup_quantity,
    _safe_fraction,
    _sanitize_broker_url,
    _storage_summary,
)


class _DummySection(ControlSectionWidget):
    def __init__(self, show_refresh_button=True, fail=False):
        self.fail = fail
        self.refresh_calls = 0
        super().__init__([], show_refresh_button=show_refresh_button)

    def _do_refresh(self):
        self.refresh_calls += 1
        if self.fail:
            raise RuntimeError("boom")


@pytest.fixture
def run_threads_synchronously(monkeypatch):
    """Run refresh()'s worker inline instead of on a background thread, so
    assertions don't race the thread's completion."""

    class _SyncThread:
        def __init__(self, target, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(threading, "Thread", _SyncThread)


def test_refresh_calls_do_refresh(run_threads_synchronously):
    widget = _DummySection(show_refresh_button=True)

    assert widget.refresh_button is not None
    assert widget._last_updated is not None

    widget.refresh()
    assert widget.refresh_calls == 1
    assert widget.refresh_button.disabled is False
    assert widget.info.value == ""
    assert "Last updated" in widget._last_updated.value


def test_refresh_guards_reentry(run_threads_synchronously, monkeypatch):
    widget = _DummySection()
    monkeypatch.setattr(widget, "_refreshing", True)
    widget.refresh()
    assert widget.refresh_calls == 0


def test_refresh_shows_error_on_exception(run_threads_synchronously):
    widget = _DummySection(fail=True, show_refresh_button=True)
    assert widget.refresh_button is not None
    assert widget._last_updated is not None

    widget.refresh()

    assert "Failed to refresh" in widget.info.value
    assert widget.refresh_button.disabled is False
    assert widget._refreshing is False
    assert widget._last_updated.value == ""


@pytest.mark.parametrize(
    "url, expected",
    [
        ("amqp://guest:guest@localhost:5672", "amqp://localhost:5672"),
        ("amqp://localhost:5672", "amqp://localhost:5672"),
        # An unescaped `@` inside the userinfo segment must still be fully
        # stripped, not just up to the first `@`.
        ("amqp://user:p@ssword@localhost:5672", "amqp://localhost:5672"),
        # The credential-bearing URL may be embedded in surrounding text,
        # as `str(broker)` produces for RabbitMQ.
        (
            "RabbitMQ v3.9 @ amqp://guest:guest@localhost:5672",
            "RabbitMQ v3.9 @ amqp://localhost:5672",
        ),
    ],
)
def test_sanitize_broker_url(url, expected):
    assert _sanitize_broker_url(url) == expected


def test_storage_summary_psql_dos():
    profile = SimpleNamespace(
        storage_backend="core.psql_dos",
        storage_config={
            "database_name": "aiidadb",
            "database_hostname": "localhost",
            "database_port": 5432,
            "database_password": "s3cr3t",
        },
    )
    summary = _storage_summary(profile)
    assert summary is not None
    assert "aiidadb" in summary
    assert "localhost:5432" in summary
    assert "s3cr3t" not in summary


def test_storage_summary_sqlite_dos():
    profile = SimpleNamespace(storage_backend="core.sqlite_dos", storage_config={})
    assert _storage_summary(profile) == "SQLite database + file repository"


def test_storage_summary_unknown_backend_returns_none():
    profile = SimpleNamespace(storage_backend="core.unknown", storage_config={})
    assert _storage_summary(profile) is None


def test_storage_summary_psql_dos_missing_key_returns_none():
    profile = SimpleNamespace(
        storage_backend="core.psql_dos",
        storage_config={"database_name": "aiidadb"},  # missing host/port
    )
    assert _storage_summary(profile) is None


def test_probe_daemon(aiida_profile):
    widget = AiidaStatusOverviewWidget()

    def _fake_daemon(**kwargs):
        return cast(_DaemonClient, SimpleNamespace(**kwargs))

    widget._daemon = _fake_daemon(
        is_daemon_running=True, get_number_of_workers=lambda: 3
    )
    row = widget._probe_daemon()
    assert State.OK.color in row
    assert "3 worker" in row

    widget._daemon = _fake_daemon(
        is_daemon_running=True, get_number_of_workers=lambda: 0
    )
    row = widget._probe_daemon()
    assert State.WARNING.color in row
    assert "0 worker" in row

    widget._daemon = _fake_daemon(is_daemon_running=False, get_number_of_workers=None)
    row = widget._probe_daemon()
    assert State.WARNING.color in row
    assert "not running" in row

    def _raise():
        raise DaemonException("stopped")

    widget._daemon = _fake_daemon(is_daemon_running=True, get_number_of_workers=_raise)
    row = widget._probe_daemon()
    assert State.WARNING.color in row
    assert "not running" in row


def test_probe_profile_matches_default(aiida_profile, monkeypatch):
    widget = AiidaStatusOverviewWidget()
    widget._profile = aiida_profile
    monkeypatch.setattr(
        control_module, "_current_default_profile_name", lambda: aiida_profile.name
    )
    row = widget._probe_profile()
    assert State.OK.color in row


def test_probe_profile_drifted_from_default(aiida_profile, monkeypatch):
    widget = AiidaStatusOverviewWidget()
    widget._profile = aiida_profile
    monkeypatch.setattr(
        control_module, "_current_default_profile_name", lambda: "some-other-profile"
    )
    row = widget._probe_profile()
    assert State.WARNING.color in row
    assert "reload the page" in row


def test_probe_profile_default_read_failure_falls_back_to_loaded(
    aiida_profile, monkeypatch
):
    def _raise():
        raise OSError("boom")

    widget = AiidaStatusOverviewWidget()
    widget._profile = aiida_profile
    monkeypatch.setattr(control_module, "_current_default_profile_name", _raise)
    row = widget._probe_profile()
    assert State.OK.color in row


def test_status_overview_do_refresh(aiida_profile, run_threads_synchronously):
    widget = AiidaStatusOverviewWidget()
    widget.refresh()
    table = widget._status.value
    assert aiida_profile.name in table

    # Storage/broker/daemon probes touch real services that may not be
    # available in every test environment; only version/config/profile are
    # guaranteed to succeed here.
    rows = re.findall(r"<tr>.*?</tr>", table)
    checked_rows = [
        row
        for row in rows
        if any(f"<b>{label}</b>" in row for label in ("version", "config", "profile"))
    ]
    assert len(checked_rows) == 3
    assert not any(State.ERROR.color in row for row in checked_rows)


def test_status_overview_no_broker(
    aiida_profile, run_threads_synchronously, monkeypatch
):
    monkeypatch.setattr(control_module.manage.get_manager(), "get_broker", lambda: None)
    widget = AiidaStatusOverviewWidget()
    widget.refresh()
    assert "No broker configured" in widget._status.value


@pytest.mark.parametrize(
    "n, expected",
    [
        (0, "0.0 B"),
        (512, "512.0 B"),
        (1023, "1023.0 B"),
        (1024, "1.0 KiB"),
        (3.4 * 1024**3, "3.4 GiB"),
        (1024**4, "1.0 TiB"),
        (5 * 1024**5, "5120.0 TiB"),  # PiB overflows into TiB, not a new unit
    ],
)
def test_format_bytes(n, expected):
    assert _format_bytes(n) == expected


@pytest.fixture
def cgroup_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(control_module, "_CGROUP_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def meminfo_path(tmp_path, monkeypatch):
    path = tmp_path / "meminfo"
    monkeypatch.setattr(control_module, "_MEMINFO_PATH", path)
    return path


def test_read_cgroup_quantity(cgroup_dir):
    (cgroup_dir / "memory.max").write_text("1073741824\n")
    assert _read_cgroup_quantity("memory.max") == 1073741824

    (cgroup_dir / "memory.max").write_text("max\n")
    assert _read_cgroup_quantity("memory.max") is None

    (cgroup_dir / "memory.max").unlink()
    assert _read_cgroup_quantity("memory.max") is None

    (cgroup_dir / "memory.max").write_text("not-a-number\n")
    assert _read_cgroup_quantity("memory.max") is None


def test_memory_status_limited_subtracts_inactive_file(cgroup_dir):
    (cgroup_dir / "memory.current").write_text("2147483648")  # 2 GiB
    (cgroup_dir / "memory.stat").write_text(
        "active_file 100\ninactive_file 536870912\nother 5\n"  # 0.5 GiB
    )
    (cgroup_dir / "memory.max").write_text("4294967296")  # 4 GiB

    used, total = _memory_status()
    assert used == 2147483648 - 536870912
    assert total == 4294967296


def test_memory_status_unlimited_uses_host_wide_used_and_total(
    cgroup_dir, meminfo_path
):
    (cgroup_dir / "memory.current").write_text("2147483648")
    # No memory.max file: unlimited, so used/total come entirely from
    # /proc/meminfo (host-wide) rather than this container's own current -
    # a small container shouldn't look like it has all of MemTotal free
    # when the rest of the host/VM has already claimed most of it.
    meminfo_path.write_text(
        "MemTotal:       8388608 kB\nMemAvailable:   1048576 kB\nMemFree:  524288 kB\n"
    )

    used, total = _memory_status()
    assert total == 8 * 1024**3
    assert used == 8 * 1024**3 - 1 * 1024**3


def test_memory_status_missing_memory_stat_raises(cgroup_dir):
    (cgroup_dir / "memory.current").write_text("2147483648")
    (cgroup_dir / "memory.max").write_text("4294967296")
    # No memory.stat file: same "shouldn't happen in Docker" story as a
    # missing memory.current - surface it rather than guessing "used".

    with pytest.raises(RuntimeError, match="memory.stat"):
        _memory_status()


def test_memory_status_memory_stat_missing_inactive_file_raises(cgroup_dir):
    (cgroup_dir / "memory.current").write_text("2147483648")
    (cgroup_dir / "memory.max").write_text("4294967296")
    (cgroup_dir / "memory.stat").write_text("active_file 100\nother 5\n")

    with pytest.raises(RuntimeError, match="memory.stat"):
        _memory_status()


def test_memory_status_no_cgroup_raises(cgroup_dir):
    # No memory.current file at all: not running under cgroup v2, which
    # shouldn't happen in AiiDAlab's Docker deployment - surface it as an
    # error rather than silently reporting meaningless host-wide numbers.
    with pytest.raises(RuntimeError, match="cgroup v2"):
        _memory_status()


def test_cpu_status_quota_present(cgroup_dir, monkeypatch):
    (cgroup_dir / "cpu.max").write_text("200000 100000\n")
    monkeypatch.setattr(control_module.os, "getloadavg", lambda: (1.5, 1.0, 1.0))

    load_1min, effective_cpus = _cpu_status()
    assert load_1min == 1.5
    assert effective_cpus == 2.0


def test_cpu_status_quota_max_falls_back_to_cpu_count(cgroup_dir, monkeypatch):
    (cgroup_dir / "cpu.max").write_text("max 100000\n")
    monkeypatch.setattr(control_module.os, "getloadavg", lambda: (0.5, 0.5, 0.5))
    monkeypatch.setattr(control_module.os, "cpu_count", lambda: 4)

    load_1min, effective_cpus = _cpu_status()
    assert load_1min == 0.5
    assert effective_cpus == 4.0


def test_cpu_status_no_cgroup_raises(cgroup_dir, monkeypatch):
    # No cpu.max file at all: not running under cgroup v2, which shouldn't
    # happen in AiiDAlab's Docker deployment - surface it as an error
    # rather than silently reporting a host-wide CPU count as this
    # container's own.
    monkeypatch.setattr(control_module.os, "getloadavg", lambda: (0.5, 0.5, 0.5))

    with pytest.raises(RuntimeError, match="cgroup v2"):
        _cpu_status()


def test_cpu_status_malformed_quota_raises(cgroup_dir, monkeypatch):
    (cgroup_dir / "cpu.max").write_text("not-a-number 100000\n")
    monkeypatch.setattr(control_module.os, "getloadavg", lambda: (0.5, 0.5, 0.5))

    with pytest.raises(RuntimeError, match="cgroup v2"):
        _cpu_status()


def test_safe_fraction_zero_total_raises():
    with pytest.raises(ValueError, match="unavailable"):
        _safe_fraction(1, 0)


def test_safe_fraction_none_total_raises():
    with pytest.raises(ValueError, match="unavailable"):
        _safe_fraction(1, None)


def test_safe_fraction_normal():
    assert _safe_fraction(1, 4) == 0.25


@pytest.mark.parametrize(
    "fraction, expected_style",
    [
        (0.0, "success"),
        (0.74, "success"),
        (0.75, "warning"),
        (0.89, "warning"),
        (0.90, "danger"),
        (1.0, "danger"),
    ],
)
def test_system_resources_bar_style(fraction, expected_style):
    assert SystemResourcesWidget._bar_style(fraction) == expected_style


@pytest.fixture
def stub_resource_probes(monkeypatch):
    monkeypatch.setattr(control_module, "_memory_status", lambda: (512, 1024))
    monkeypatch.setattr(control_module, "_cpu_status", lambda: (1.0, 4.0))
    monkeypatch.setattr(control_module, "_disk_status", lambda: (200, 1000))


def test_system_resources_do_refresh_success(
    run_threads_synchronously, stub_resource_probes
):
    widget = SystemResourcesWidget()
    widget.refresh()

    assert widget._memory_bar.value == pytest.approx(0.5)
    assert widget._memory_bar.bar_style == "success"
    assert "50%" in widget._memory_label.value

    assert widget._cpu_bar.value == pytest.approx(0.25)
    assert widget._cpu_bar.bar_style == "success"

    assert widget._disk_bar.value == pytest.approx(0.2)
    assert widget._disk_bar.bar_style == "success"
    assert State.ERROR.color not in widget._disk_label.value


def test_system_resources_do_refresh_partial_failure(
    run_threads_synchronously, stub_resource_probes, monkeypatch
):
    def _raise():
        raise RuntimeError("cgroup v2 memory accounting unavailable")

    monkeypatch.setattr(control_module, "_memory_status", _raise)

    widget = SystemResourcesWidget()
    widget.refresh()

    # Memory failed...
    assert widget._memory_bar.value == 0
    assert widget._memory_bar.bar_style == "danger"
    assert "cgroup v2 memory accounting unavailable" in widget._memory_label.value

    # ...but CPU and disk still updated independently.
    assert widget._cpu_bar.bar_style == "success"
    assert widget._disk_bar.bar_style == "success"


class _FakeConfig:
    """A stand-in for AiiDA's Config, recording the calls made to it."""

    def __init__(self, names, default):
        self.profiles = [SimpleNamespace(name=name) for name in names]
        self.default_profile_name = default
        self.calls = []

    def set_default_profile(self, name, overwrite=False):
        self.calls.append(("set_default_profile", name, overwrite))
        self.default_profile_name = name

    def store(self):
        self.calls.append(("store",))

    def delete_profile(self, name, delete_storage=True):
        self.calls.append(("delete_profile", name, delete_storage))
        self.profiles = [p for p in self.profiles if p.name != name]
        # Like AiiDA, keep the stale default when no profiles remain.
        if name == self.default_profile_name and self.profiles:
            self.default_profile_name = self.profiles[0].name


@pytest.fixture
def profile_widget(monkeypatch):
    """A ProfileControlWidget over a fake config with the profiles "main"
    (default), "other" and "in-use" (loaded by this page)."""
    config = _FakeConfig(["main", "other", "in-use"], default="main")
    monkeypatch.setattr(control_module, "load_config", lambda: config)
    monkeypatch.setattr(
        control_module, "get_profile", lambda: SimpleNamespace(name="in-use")
    )
    widget = ProfileControlWidget()
    widget._do_refresh()
    return widget, config


def _profile_rows(widget):
    return {row.children[0].value.split(" ")[0]: row for row in widget._rows.children}


def test_profile_rows(profile_widget):
    widget, _ = profile_widget
    rows = _profile_rows(widget)
    assert list(rows) == ["main", "other", "in-use"]

    assert "default" in rows["main"].children[0].value
    assert rows["main"].make_default_button.disabled is True
    assert rows["main"].delete_button.disabled is False

    assert "(in use)" in rows["in-use"].children[0].value
    assert rows["in-use"].delete_button.disabled is True
    assert rows["in-use"].make_default_button.disabled is False


def test_profile_names_are_escaped(monkeypatch):
    config = _FakeConfig(["<b>bold</b>"], default=None)
    monkeypatch.setattr(control_module, "load_config", lambda: config)
    monkeypatch.setattr(control_module, "get_profile", lambda: None)
    widget = ProfileControlWidget()
    widget._do_refresh()
    assert "&lt;b&gt;bold&lt;/b&gt;" in widget._rows.children[0].children[0].value


def test_profile_make_default(profile_widget):
    widget, config = profile_widget
    _profile_rows(widget)["other"].make_default_button.click()

    assert config.calls == [("set_default_profile", "other", True), ("store",)]
    assert 'Profile "other" is now the default.' in html.unescape(widget.info.value)
    rows = _profile_rows(widget)
    assert "default" in rows["other"].children[0].value
    assert "default" not in rows["main"].children[0].value


def test_profile_delete_needs_confirmation(profile_widget):
    widget, config = profile_widget
    _profile_rows(widget)["other"].delete_button.click()

    assert widget._confirm_box.layout.display == ""
    assert "<b>other</b>" in widget._confirm_text.value
    assert widget._delete_storage.value is False
    assert config.calls == []

    widget._delete_storage.value = True
    # Retargeting resets the checkbox and names the new target.
    _profile_rows(widget)["main"].delete_button.click()
    assert "<b>main</b>" in widget._confirm_text.value
    assert widget._delete_storage.value is False

    widget._on_confirm_delete()
    assert config.calls == [("delete_profile", "main", False)]
    assert widget._confirm_box.layout.display == "none"
    # Deleting the default makes AiiDA pick a new one; the message says so.
    assert 'Profile "main" deleted.' in html.unescape(widget.info.value)
    assert '"other" is now the default profile.' in html.unescape(widget.info.value)
    assert list(_profile_rows(widget)) == ["other", "in-use"]


def test_profile_delete_with_data(profile_widget):
    widget, config = profile_widget
    _profile_rows(widget)["other"].delete_button.click()
    widget._delete_storage.value = True
    widget._on_confirm_delete()
    assert config.calls == [("delete_profile", "other", True)]
    assert "now the default" not in widget.info.value


def test_profile_delete_last_default(monkeypatch):
    # The loaded profile was removed by another process, so the last
    # profile, the default, can be deleted: no new default is claimed.
    config = _FakeConfig(["main"], default="main")
    monkeypatch.setattr(control_module, "load_config", lambda: config)
    monkeypatch.setattr(
        control_module, "get_profile", lambda: SimpleNamespace(name="gone")
    )
    widget = ProfileControlWidget()
    widget._do_refresh()
    _profile_rows(widget)["main"].delete_button.click()
    widget._on_confirm_delete()

    message = html.unescape(widget.info.value)
    assert 'Profile "main" deleted.' in message
    assert "now the default" not in message
    assert not widget._rows.children


def test_profile_delete_cancel(profile_widget):
    widget, config = profile_widget
    _profile_rows(widget)["other"].delete_button.click()
    widget._dismiss_confirmation()
    assert widget._confirm_box.layout.display == "none"
    widget._on_confirm_delete()
    assert config.calls == []


def test_profile_refresh_dismisses_confirmation(profile_widget):
    widget, config = profile_widget
    _profile_rows(widget)["other"].delete_button.click()
    widget._do_refresh()
    assert widget._confirm_box.layout.display == "none"
    widget._on_confirm_delete()
    assert config.calls == []


def test_profile_delete_failure_is_shown(profile_widget, monkeypatch):
    widget, config = profile_widget

    def _raise(name, delete_storage=True):
        raise RuntimeError("database is busy")

    monkeypatch.setattr(config, "delete_profile", _raise)
    _profile_rows(widget)["other"].delete_button.click()
    widget._on_confirm_delete()
    assert State.ERROR.color in widget.info.value
    assert 'Failed to delete profile "other": database is busy' in html.unescape(
        widget.info.value
    )


@pytest.fixture
def restore_config_file(aiida_profile):
    """Restore the test config file after a test that writes to it."""
    path = Path(load_config().filepath)
    content = path.read_text()
    yield
    path.write_text(content)


def test_profile_make_default_keeps_external_changes(
    aiida_profile, restore_config_file
):
    config = load_config()
    config.add_profile(Profile("other", aiida_profile.dictionary))
    config.store()
    widget = ProfileControlWidget()
    widget._do_refresh()

    # Another process (e.g. `verdi presto` in a terminal) adds a profile
    # after the page loaded the config.
    external = load_config()
    external.add_profile(Profile("external", aiida_profile.dictionary))
    external.store()

    _profile_rows(widget)["other"].make_default_button.click()

    config = load_config()
    assert config.default_profile_name == "other"
    assert "external" in config.profile_names
