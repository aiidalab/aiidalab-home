from threading import Event
from types import SimpleNamespace

from home import process as home_process


def test_process_monitor_thread_is_daemon(monkeypatch):
    monkeypatch.setattr(
        home_process.orm, "load_node", lambda _: SimpleNamespace(is_sealed=False)
    )
    monitor = home_process.ProcessMonitor(timeout=0.01)
    monitor.value = "process-uuid"
    thread = monitor._monitor_thread
    assert thread is not None
    try:
        assert thread.is_alive()
        assert thread.daemon
    finally:
        monitor.value = None
    assert not thread.is_alive()


def test_process_list_autoupdate_thread_stops(monkeypatch):
    refreshed = Event()
    monkeypatch.setattr(
        home_process.ProcessListWidget, "update", lambda _: refreshed.set()
    )
    widget = home_process.ProcessListWidget()
    widget.start_autoupdate(update_interval=0.01)
    thread = widget._autoupdate_thread
    assert thread is not None
    try:
        assert refreshed.wait(timeout=2)
        assert thread.is_alive()
        assert thread.daemon
        widget.start_autoupdate(update_interval=0.01)
        assert widget._autoupdate_thread is thread
    finally:
        widget.stop_autoupdate()
    assert not thread.is_alive()
