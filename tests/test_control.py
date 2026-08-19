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
    _DaemonClient,
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
