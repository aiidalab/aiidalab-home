import html
import logging
import re
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from aiida import orm
from aiida.engine.daemon.client import DaemonException, DaemonNotRunningException
from aiida.engine.processes import control as process_control
from aiida.manage.configuration import Profile, load_config
from aiida.storage.log import STORAGE_LOGGER

import home.control as control_module
from home.control import (
    AiidaStatusOverviewWidget,
    ControlSectionWidget,
    DaemonControlWidget,
    ProcessControlWidget,
    ProfileControlWidget,
    State,
    StorageWidget,
    SystemResourcesWidget,
    _cpu_status,
    _DaemonClient,
    _du_bytes,
    _format_bytes,
    _humanize_age,
    _ListLogHandler,
    _memory_status,
    _probe_daemon_workers,
    _read_cgroup_quantity,
    _read_log_tail,
    _repository_path,
    _safe_fraction,
    _sanitize_broker_url,
    _storage_summary,
    _worker_table_html,
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


def test_repository_path_psql_dos():
    profile = SimpleNamespace(
        storage_backend="core.psql_dos",
        storage_config={"repository_uri": "file:///home/user/my%20aiida/repository"},
    )
    assert str(_repository_path(profile)) == "/home/user/my aiida/repository"


def test_repository_path_sqlite_dos():
    profile = SimpleNamespace(
        storage_backend="core.sqlite_dos",
        storage_config={"filepath": "/home/user/.aiida/storage"},
    )
    assert str(_repository_path(profile)) == "/home/user/.aiida/storage"


def test_repository_path_unknown_backend_raises():
    profile = SimpleNamespace(storage_backend="core.unknown", storage_config={})
    with pytest.raises(ValueError, match="core.unknown"):
        _repository_path(profile)


def test_du_bytes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 1000)
    b.write_bytes(b"x" * 2345)
    # Allocated blocks (st_blocks is in 512-byte units), not apparent size.
    assert _du_bytes(a) == a.stat().st_blocks * 512
    assert (
        _du_bytes(tmp_path) == sum(p.stat().st_blocks for p in (tmp_path, a, b)) * 512
    )


def test_du_bytes_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        _du_bytes(tmp_path / "missing")


def test_du_bytes_trusts_output_despite_nonzero_exit(tmp_path, monkeypatch):
    # du exits nonzero when a file vanishes mid-scan, but still prints a total.
    def _run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout=f"123\t{tmp_path}\n")

    monkeypatch.setattr(control_module.subprocess, "run", _run)
    assert _du_bytes(tmp_path) == 123


def test_du_bytes_unparsable_output_raises(tmp_path, monkeypatch):
    def _run(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, stdout="")

    monkeypatch.setattr(control_module.subprocess, "run", _run)
    with pytest.raises(RuntimeError, match="could not determine the size"):
        _du_bytes(tmp_path)


def test_list_log_handler_collects_messages():
    test_logger = logging.getLogger("home.tests.list_log_handler")
    handler = _ListLogHandler()
    test_logger.addHandler(handler)
    try:
        test_logger.warning("packing %d files", 3)
    finally:
        test_logger.removeHandler(handler)
    assert handler.lines == ["packing 3 files"]


def test_storage_do_refresh(
    aiida_profile, run_threads_synchronously, monkeypatch, tmp_path
):
    # A missing apps directory must fail only its own row.
    monkeypatch.setattr(control_module, "AIIDALAB_APPS", str(tmp_path / "missing"))
    widget = StorageWidget()
    widget.refresh()
    table = widget._table.value
    assert aiida_profile.name in table

    rows = re.findall(r"<tr>.*?</tr>", table)
    assert len(rows) == 5
    failed = [row for row in rows if State.ERROR.color in row]
    assert len(failed) == 1
    assert "<b>Installed apps</b>" in failed[0]
    assert "does not exist" in failed[0]


def test_storage_refresh_skipped_while_maintaining(
    aiida_profile, run_threads_synchronously, monkeypatch
):
    widget = StorageWidget()
    calls = []
    monkeypatch.setattr(widget, "_do_refresh", lambda: calls.append(1))

    widget._maintaining = True
    widget.refresh()
    assert calls == []

    widget._maintaining = False
    widget.refresh()
    assert calls == [1]


class _FakeStorage:
    def __init__(self):
        self.maintain_calls = []

    def maintain(self, full, dry_run):
        self.maintain_calls.append((full, dry_run))
        STORAGE_LOGGER.info("Deleting 0 unreferenced objects ...")
        STORAGE_LOGGER.getChild("disk_object_store").info("Packing <loose> files")


@pytest.fixture
def storage_widget(aiida_profile, run_threads_synchronously, monkeypatch):
    """A StorageWidget with a fake storage, a stopped fake daemon and a
    stubbed table refresh."""
    storage = _FakeStorage()
    monkeypatch.setattr(
        control_module.manage.get_manager(), "get_profile_storage", lambda: storage
    )
    widget = StorageWidget()
    widget._daemon = cast(_DaemonClient, SimpleNamespace(is_daemon_running=False))
    monkeypatch.setattr(widget, "_do_refresh", lambda: None)
    return widget, storage


def _assert_controls_enabled(widget):
    assert widget._maintaining is False
    assert widget._maintain_button.disabled is False
    assert widget._dry_run_checkbox.disabled is False
    assert widget._full_checkbox.disabled is False
    assert widget.refresh_button.disabled is False


def test_storage_full_maintenance_refused_while_daemon_runs(storage_widget):
    widget, storage = storage_widget
    widget._daemon = cast(_DaemonClient, SimpleNamespace(is_daemon_running=True))
    widget._full_checkbox.value = True

    widget._maintain_button.click()

    assert storage.maintain_calls == []
    assert State.WARNING.color in widget._maintain_output.value
    assert "Stop the daemon first" in widget._maintain_output.value
    _assert_controls_enabled(widget)


def test_storage_dry_run_captures_info_log(storage_widget):
    widget, storage = storage_widget
    level_before = STORAGE_LOGGER.level

    widget._maintain_button.click()

    assert storage.maintain_calls == [(False, True)]
    output = widget._maintain_output.value
    assert "Dry run finished." in output
    # INFO messages from the logger and its children, escaped.
    assert "Deleting 0 unreferenced objects ..." in output
    assert "Packing &lt;loose&gt; files" in output
    assert "Reclaimed" not in output
    assert STORAGE_LOGGER.level == level_before
    assert not any(isinstance(h, _ListLogHandler) for h in STORAGE_LOGGER.handlers)
    _assert_controls_enabled(widget)


def test_storage_maintenance_reports_reclaimed_space(storage_widget, monkeypatch):
    widget, storage = storage_widget
    sizes = iter([1000, 400])
    monkeypatch.setattr(
        control_module, "_repository_size_or_none", lambda profile: next(sizes)
    )
    refreshes = []
    monkeypatch.setattr(widget, "_do_refresh", lambda: refreshes.append(1))
    widget._dry_run_checkbox.value = False

    widget._maintain_button.click()

    assert storage.maintain_calls == [(False, False)]
    assert "Maintenance finished." in widget._maintain_output.value
    assert "Reclaimed 600.0 B" in widget._maintain_output.value
    assert refreshes == [1]
    _assert_controls_enabled(widget)


def test_storage_maintenance_failure_is_shown(storage_widget):
    widget, storage = storage_widget

    def _raise(full, dry_run):
        raise RuntimeError("profile is locked")

    storage.maintain = _raise

    widget._maintain_button.click()

    assert State.ERROR.color in widget._maintain_output.value
    assert "Maintenance failed: profile is locked" in widget._maintain_output.value
    _assert_controls_enabled(widget)


def test_storage_dry_run_on_real_storage(aiida_profile, run_threads_synchronously):
    # Guards the log capture against upstream logger renames.
    widget = StorageWidget()
    widget._maintain_button.click()
    output = widget._maintain_output.value
    assert "Dry run finished." in output
    assert "unreferenced objects" in output


@pytest.mark.parametrize(
    "seconds, expected",
    [
        (0, "0s"),
        (59.9, "59s"),
        (60, "1m"),
        (3599, "59m"),
        (3600, "1h 00m"),
        (4 * 3600 + 5 * 60, "4h 05m"),
        (86399, "23h 59m"),
        (86400, "1d 0h"),
        (100905.8, "1d 4h"),
        (None, "?"),
        ("garbage", "?"),
        (float("inf"), "?"),
    ],
)
def test_humanize_age(seconds, expected):
    assert _humanize_age(seconds) == expected


def test_read_log_tail(tmp_path):
    log = tmp_path / "daemon.log"
    log.write_text("".join(f"line {i}\n" for i in range(100)))
    tail = _read_log_tail(log, 50)
    assert tail.splitlines() == [f"line {i}" for i in range(50, 100)]


def test_read_log_tail_reads_at_most_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(control_module, "_LOG_TAIL_BYTES", 20)
    log = tmp_path / "daemon.log"
    log.write_text("first line\n" + "x" * 100 + "\nlast line\n")
    assert _read_log_tail(log, 50).splitlines()[-1] == "last line"
    assert "first line" not in _read_log_tail(log, 50)


def test_read_log_tail_empty_and_missing(tmp_path):
    log = tmp_path / "daemon.log"
    assert _read_log_tail(log, 50) == "Log file not found"
    log.write_text("")
    assert _read_log_tail(log, 50) == "(log is empty)"


def test_worker_table_html():
    workers = {
        "1234": {
            "wid": 1,
            "cpu": 12.34,
            "mem": 0.5,
            "mem_info1": "150M",
            "age": 3700,
        },
        "5678": {"wid": 2, "cpu": "N/A", "mem": "N/A", "mem_info1": "N/A"},
        # circus reports a message for a worker that has just stopped.
        "9999": "No such process (stopped?)",
    }
    table = _worker_table_html(workers)
    rows = re.findall(r"<tr>.*?</tr>", table)
    assert len(rows) == 4  # header + 3 workers
    assert "1234" in rows[1]
    assert "12.3%" in rows[1]
    assert "150M" in rows[1]
    assert "1h 01m" in rows[1]
    assert "?" in rows[2]
    assert "9999" in rows[3]


@pytest.mark.parametrize("workers", [None, {}])
def test_worker_table_html_no_workers(workers):
    assert _worker_table_html(workers) == ""


def _fake_daemon_client(**kwargs):
    return SimpleNamespace(**kwargs)


def test_probe_daemon_workers_not_running():
    client = _fake_daemon_client(is_daemon_running=False, get_worker_info=None)
    assert _probe_daemon_workers(client) == (
        State.WARNING,
        "Daemon is not running",
        None,
    )


def test_probe_daemon_workers_running():
    info = {"info": {"1": {"wid": 1}, "2": {"wid": 2}}}
    client = _fake_daemon_client(is_daemon_running=True, get_worker_info=lambda: info)
    state, text, workers = _probe_daemon_workers(client)
    assert state == State.OK
    assert "2 worker" in text
    assert workers == info["info"]


def test_probe_daemon_workers_running_without_workers():
    client = _fake_daemon_client(
        is_daemon_running=True, get_worker_info=lambda: {"info": {}}
    )
    state, text, workers = _probe_daemon_workers(client)
    assert state == State.WARNING
    assert "0 workers" in text
    assert workers == {}


def test_probe_daemon_workers_stopped_in_between():
    def _raise():
        raise DaemonNotRunningException("The daemon is not running.")

    client = _fake_daemon_client(is_daemon_running=True, get_worker_info=_raise)
    assert _probe_daemon_workers(client) == (
        State.WARNING,
        "Daemon is not running",
        None,
    )


def test_probe_daemon_workers_unreachable_is_an_error():
    def _raise():
        raise DaemonException("stale PID file")

    client = _fake_daemon_client(is_daemon_running=True, get_worker_info=_raise)
    assert _probe_daemon_workers(client) == (State.ERROR, "stale PID file", None)


def test_probe_daemon_workers_is_running_raises():
    class _Client:
        @property
        def is_daemon_running(self):
            raise RuntimeError("no profile")

    assert _probe_daemon_workers(_Client()) == (State.ERROR, "no profile", None)


class _FakeDaemon:
    """A DaemonClient stand-in recording the calls made to it."""

    daemon_log_file = "/nonexistent/aiida-test.log"

    def __init__(self, running=True, workers=2):
        self.running = running
        self.workers = workers
        self.calls = []

    @property
    def is_daemon_running(self):
        return self.running

    def get_worker_info(self):
        if not self.running:
            raise DaemonNotRunningException("The daemon is not running.")
        return {"info": {str(pid): {"wid": pid} for pid in range(self.workers)}}

    def get_number_of_workers(self):
        return len(self.get_worker_info()["info"])

    def start_daemon(self, number_workers):
        self.calls.append("start")
        self.running = True
        self.workers = number_workers

    def stop_daemon(self):
        self.calls.append("stop")
        self.running = False

    def restart_daemon(self):
        self.calls.append("restart")
        if not self.running:
            raise DaemonNotRunningException("The daemon is not running.")

    def increase_workers(self, number):
        self.calls.append(("increase", number))
        self.workers += number

    def decrease_workers(self, number):
        self.calls.append(("decrease", number))
        self.workers -= number


@pytest.fixture
def daemon_widget(aiida_profile, run_threads_synchronously, monkeypatch):
    """A DaemonControlWidget driving a fake daemon."""
    daemon = _FakeDaemon()
    widget = DaemonControlWidget()
    monkeypatch.setattr(widget, "_daemon", daemon)
    return widget, daemon


def test_daemon_buttons_when_stopped(daemon_widget):
    widget, daemon = daemon_widget
    daemon.running = False
    widget._update_status()
    assert "not running" in widget._status.value
    assert widget.add_worker_button.disabled is True
    assert widget.remove_worker_button.disabled is True
    assert widget.remove_worker_button.tooltip == "The daemon is not running"


def test_daemon_remove_worker_disabled_at_one_worker(daemon_widget):
    widget, daemon = daemon_widget
    daemon.workers = 1
    widget._update_status()
    assert widget.add_worker_button.disabled is False
    assert widget.remove_worker_button.disabled is True
    assert widget.remove_worker_button.tooltip == "At least one worker is required"

    daemon.workers = 2
    widget._update_status()
    assert widget.remove_worker_button.disabled is False
    assert "2 worker" in widget._status.value
    assert widget._worker_table.value.count("<tr>") == 3


def test_daemon_warns_about_more_workers_than_cpus(daemon_widget, monkeypatch):
    widget, _ = daemon_widget
    monkeypatch.setattr(control_module.os, "cpu_count", lambda: 1)
    widget._update_status()
    assert "more workers than the 1 available CPUs" in widget._status.value


def _assert_action_buttons_enabled(widget):
    assert widget._busy is False
    for button in (
        widget.start_button,
        widget.stop_button,
        widget.restart_button,
        widget.refresh_button,
    ):
        assert button.disabled is False


def _configure_default_workers(monkeypatch, number):
    """Make the `daemon.default_workers` config option return `number`."""
    options = {"daemon.default_workers": number}
    monkeypatch.setattr(control_module.manage, "get_config_option", options.__getitem__)


def test_daemon_stop_and_start(daemon_widget, monkeypatch):
    widget, daemon = daemon_widget
    _configure_default_workers(monkeypatch, 3)

    widget.stop_button.click()
    assert daemon.calls == ["stop"]
    assert "The daemon has been stopped." in widget.info.value
    assert "not running" in widget._status.value
    _assert_action_buttons_enabled(widget)

    widget.start_button.click()
    assert daemon.calls == ["stop", "start"]
    assert "The daemon has been started." in widget.info.value
    assert "3 worker" in widget._status.value
    _assert_action_buttons_enabled(widget)


def test_daemon_start_when_running_only_warns(daemon_widget):
    widget, daemon = daemon_widget
    widget.start_button.click()
    assert daemon.calls == []
    assert "already running" in widget.info.value


def test_daemon_restart_falls_back_to_start(daemon_widget, monkeypatch):
    widget, daemon = daemon_widget
    _configure_default_workers(monkeypatch, 3)
    daemon.running = False
    widget.restart_button.click()
    assert daemon.calls == ["restart", "start"]
    assert daemon.workers == 3
    assert "The daemon has been restarted." in widget.info.value


def test_daemon_restart_failure_is_shown(daemon_widget, monkeypatch):
    widget, daemon = daemon_widget

    def _raise():
        raise DaemonException("Connection to the daemon timed out.")

    monkeypatch.setattr(daemon, "restart_daemon", _raise)
    widget.restart_button.click()
    assert daemon.calls == []
    assert State.ERROR.color in widget.info.value
    assert "Failed to restart the daemon: Connection" in widget.info.value
    _assert_action_buttons_enabled(widget)


def test_daemon_failing_status_probe_after_action(daemon_widget, monkeypatch):
    widget, daemon = daemon_widget

    def _raise():
        raise RuntimeError("probe failed")

    monkeypatch.setattr(widget, "_update_status", _raise)
    widget.stop_button.click()
    assert daemon.calls == ["stop"]
    # The action's own result stays visible, followed by the probe failure.
    assert "The daemon has been stopped." in widget.info.value
    assert "Failed to refresh the status: probe failed" in widget.info.value
    _assert_action_buttons_enabled(widget)


def test_daemon_add_and_remove_worker(daemon_widget):
    widget, daemon = daemon_widget
    widget.add_worker_button.click()
    assert daemon.workers == 3
    assert "3 worker" in widget._status.value

    widget.remove_worker_button.click()
    assert daemon.workers == 2
    assert "A worker has been removed." in widget.info.value


def test_daemon_remove_last_worker_is_refused(daemon_widget):
    widget, daemon = daemon_widget
    # The button is enabled from a stale status, but the live count is 1.
    daemon.workers = 1
    widget.remove_worker_button.disabled = False
    widget.remove_worker_button.click()
    assert daemon.calls == []
    assert "at least one worker is required" in widget.info.value


def test_daemon_refused_command_is_shown(daemon_widget, monkeypatch):
    widget, daemon = daemon_widget
    # circus refuses a command while another holds its lock (here, a removed
    # worker still shutting down) and says so in its reply, without raising.
    refusal = {
        "status": "error",
        "reason": "arbiter is already running watcher_decr command",
    }
    monkeypatch.setattr(daemon, "decrease_workers", lambda number: refusal)
    widget.remove_worker_button.click()
    assert State.ERROR.color in widget.info.value
    assert "Failed to remove a worker: arbiter is already running" in widget.info.value
    _assert_action_buttons_enabled(widget)


def test_daemon_refresh_skipped_while_busy(daemon_widget, monkeypatch):
    widget, _ = daemon_widget
    calls = []
    monkeypatch.setattr(widget, "_do_refresh", lambda: calls.append(1))

    widget._busy = True
    widget.refresh()
    assert calls == []

    widget._busy = False
    widget.refresh()
    assert calls == [1]


def test_daemon_log_loads_when_opened(daemon_widget, tmp_path):
    widget, daemon = daemon_widget
    log = tmp_path / "daemon.log"
    log.write_text("worker started <ok>\n")
    daemon.daemon_log_file = str(log)

    assert widget._log_content.value == ""
    widget._log_accordion.selected_index = 0
    assert "worker started &lt;ok&gt;" in widget._log_content.value

    # While open, the tail follows status updates.
    log.write_text("worker stopped\n")
    widget._update_status()
    assert "worker stopped" in widget._log_content.value


def test_daemon_do_refresh_real_client(aiida_profile, run_threads_synchronously):
    # No daemon runs for the temporary test profile.
    widget = DaemonControlWidget()
    widget.refresh()
    assert "Daemon is not running" in widget._status.value
    assert widget.add_worker_button.disabled is True


@pytest.fixture
def process_widget(aiida_profile, run_threads_synchronously, monkeypatch):
    """A ProcessControlWidget with a fake, running daemon."""
    widget = ProcessControlWidget()
    monkeypatch.setattr(widget, "_daemon", SimpleNamespace(is_daemon_running=True))
    return widget


def _show_rows(widget, *pks):
    """Simulate a process list update displaying the given PKs."""
    widget.process_list.current_rows = [
        {"PK": str(pk), "Process label": f"Job{pk}", "Process State": "Waiting"}
        for pk in pks
    ]
    widget.process_list.updated += 1


def test_process_options_follow_the_list(process_widget):
    widget = process_widget
    _show_rows(widget, 1, 2, 3)
    assert widget._selection.options == (
        ("1 | Job1 | Waiting", 1),
        ("2 | Job2 | Waiting", 2),
        ("3 | Job3 | Waiting", 3),
    )

    widget._selection.value = (1, 3)
    _show_rows(widget, 3, 4)
    # 1 is gone from the list; 3 stays selected.
    assert widget._selection.value == (3,)


def test_process_buttons_need_a_selection(process_widget):
    widget = process_widget
    _show_rows(widget, 1)
    assert widget.pause_button.disabled is True
    widget._selection.value = (1,)
    assert widget.pause_button.disabled is False
    assert widget.kill_button.disabled is False


def test_process_kill_needs_confirmation(process_widget, monkeypatch):
    widget = process_widget
    killed = []
    monkeypatch.setattr(
        process_control,
        "kill_processes",
        lambda nodes, timeout: killed.append([node.pk for node in nodes]),
    )
    monkeypatch.setattr(process_control, "pause_processes", lambda nodes, timeout: None)
    # Keep the stubbed rows: a real update would disarm the kill by itself.
    monkeypatch.setattr(widget.process_list, "update", lambda: None)
    first, second = orm.Int(1).store(), orm.Int(2).store()
    _show_rows(widget, first.pk, second.pk)
    widget._selection.value = (first.pk, second.pk)

    widget.kill_button.click()
    assert widget.kill_button.description == "Confirm kill (2)"
    assert killed == []

    # Changing the selection disarms.
    widget._selection.value = (first.pk,)
    assert widget.kill_button.description == "Kill"
    widget.kill_button.click()
    assert killed == []

    # So does a list update.
    _show_rows(widget, first.pk, second.pk)
    assert widget.kill_button.description == "Kill"

    # So does pausing.
    widget.kill_button.click()
    widget.pause_button.click()
    assert widget.kill_button.description == "Kill"

    # A confirmed kill is sent, and the button is reset.
    widget.kill_button.click()
    widget.kill_button.click()
    assert killed == [[first.pk]]
    assert widget.kill_button.description == "Kill"


def test_process_past_days_filter(process_widget):
    # Regression test: a bidirectional link between the day count and
    # `past_days` let "All days" overwrite the day count with -1.
    widget = process_widget
    process_list = widget.process_list
    assert widget._all_days.value is True
    assert process_list.past_days == -1
    assert widget._past_days.disabled is True

    widget._all_days.value = False
    assert widget._past_days.value == 7
    assert widget._past_days.disabled is False
    assert process_list.past_days == 7

    widget._past_days.value = 3
    assert process_list.past_days == 3

    widget._all_days.value = True
    assert process_list.past_days == -1
    assert widget._past_days.value == 3

    widget._all_days.value = False
    assert process_list.past_days == 3


def test_process_state_filter(process_widget):
    widget = process_widget
    assert widget.process_list.process_states == ["running", "waiting"]
    widget._state_filter.value = ("finished",)
    assert widget.process_list.process_states == ["finished"]


def test_process_action_refused_without_daemon(process_widget, monkeypatch):
    widget = process_widget
    paused = []
    monkeypatch.setattr(
        process_control, "pause_processes", lambda nodes, timeout: paused.append(1)
    )
    monkeypatch.setattr(widget, "_daemon", SimpleNamespace(is_daemon_running=False))
    _show_rows(widget, 1)
    widget._selection.value = (1,)

    widget.pause_button.click()

    assert paused == []
    assert State.WARNING.color in widget._action_status.value
    assert "Daemon tab" in widget._action_status.value


def test_process_action_reports_outcomes(process_widget, monkeypatch):
    widget = process_widget
    node = orm.Int(1).store()
    missing_pk = 999_999_999
    received = []

    def _pause(nodes, timeout):
        received.append([n.pk for n in nodes])
        process_control.LOGGER.report(f"Request to pause Process<{node.pk}> sent.")
        process_control.LOGGER.error(f"Process<{node.pk}> is already terminated.")

    monkeypatch.setattr(process_control, "pause_processes", _pause)
    updates = []
    monkeypatch.setattr(widget.process_list, "update", lambda: updates.append(1))
    _show_rows(widget, node.pk, missing_pk)
    widget._selection.value = (node.pk, missing_pk)

    widget.pause_button.click()

    assert received == [[node.pk]]
    status = widget._action_status.value
    assert "Pause requested for 1 process(es)" in status
    assert f"REPORT: Request to pause Process&lt;{node.pk}&gt; sent." in status
    assert f"ERROR: Process&lt;{node.pk}&gt; is already terminated." in status
    assert f"PK {missing_pk}" in status
    assert State.ERROR.color in status
    # The handler is removed again, the buttons re-enabled, the list updated.
    assert not any(
        isinstance(h, _ListLogHandler) for h in process_control.LOGGER.handlers
    )
    assert widget._busy is False
    assert widget.pause_button.disabled is False
    assert updates == [1]


def test_process_action_failure_is_shown(process_widget, monkeypatch):
    widget = process_widget
    node = orm.Int(1).store()

    def _play(nodes, timeout):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr(process_control, "play_processes", _play)
    monkeypatch.setattr(widget.process_list, "update", lambda: None)
    _show_rows(widget, node.pk)
    widget._selection.value = (node.pk,)

    widget.play_button.click()

    assert "Failed to play the process(es): broker unreachable" in (
        widget._action_status.value
    )
    assert widget.play_button.disabled is False


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
    assert (
        'Profile "other" is now the default. Reload the page to use it.'
        in html.unescape(widget.info.value)
    )
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
