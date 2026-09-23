import re
import threading
from types import SimpleNamespace
from typing import cast

import pytest
from aiida.engine.daemon.client import DaemonException

import home.control as control_module
from home.control import (
    AiidaStatusOverviewWidget,
    ControlSectionWidget,
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
