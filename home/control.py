from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import ClassVar, Protocol
from urllib.parse import unquote, urlparse

import aiida
import ipywidgets as ipw
import sqlalchemy as sa
import traitlets as tl
from aiida import get_profile, manage, orm
from aiida.common.exceptions import NotExistent
from aiida.engine.daemon.client import DaemonException, DaemonNotRunningException
from aiida.engine.processes import control as process_control
from aiida.storage.log import STORAGE_LOGGER
from aiidalab.config import AIIDALAB_APPS
from plumpy import ProcessState

from home.process import (
    HEADER_PK,
    HEADER_PROCESS_LABEL,
    HEADER_STATE,
    ProcessListWidget,
)
from home.themes import ThemeDefault as Theme

logger = logging.getLogger(__name__)


class _DaemonClient(Protocol):
    """The subset of `DaemonClient` that the control sections rely on."""

    @property
    def is_daemon_running(self) -> bool: ...

    def get_number_of_workers(self) -> int: ...


class State(str, Enum):
    color: str
    icon: str

    def __new__(cls, value, color, icon):
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.color = color
        obj.icon = icon
        return obj

    OK = "ok", Theme.COLORS.CHECK, Theme.ICONS.CHECK
    WARNING = "warning", Theme.COLORS.AIIDALAB_ORANGE, Theme.ICONS.WARNING
    ERROR = "error", Theme.COLORS.DANGER, Theme.ICONS.TIMES_CIRCLE


def _state_span(state: State, text) -> str:
    return f"<span style='color:{state.color}'>{html.escape(text)}</span>"


class ControlSectionWidget(ipw.VBox):
    """Shared anatomy for a control-page tab: description, body, footer.

    The footer (refresh button, "Last updated" label, transient feedback) and
    the threaded refresh-with-guard pattern are common to every section, so
    they live here; subclasses only provide a body and a `_do_refresh()`.
    """

    description = ""

    def __init__(self, children, show_refresh_button=True):
        self._refreshing = False

        header_children = []
        if self.description:
            header_children.append(
                ipw.HTML(
                    f"<div style='color:{Theme.COLORS.GRAY};font-size:13px;"
                    f"margin:2px 0 6px 0;'>{html.escape(self.description)}</div>"
                )
            )

        footer_children = []
        if show_refresh_button:
            self.refresh_button = ipw.Button(description="Refresh", icon="refresh")
            self.refresh_button.on_click(self.refresh)
            footer_children.append(self.refresh_button)
            self._last_updated = ipw.HTML()
            footer_children.append(self._last_updated)
        else:
            self.refresh_button = None
            self._last_updated = None
        self.info = ipw.HTML()
        footer_children.append(self.info)
        footer = ipw.HBox(footer_children)

        super().__init__(
            children=[*header_children, *children, footer],
        )
        self.layout.padding = "8px 0 0 0"

    def show_success(self, text):
        self.info.value = _state_span(State.OK, text)

    def show_warning(self, text):
        self.info.value = _state_span(State.WARNING, text)

    def show_error(self, text):
        self.info.value = _state_span(State.ERROR, text)

    def show_plain(self, text):
        self.info.value = html.escape(text)

    def refresh(self, _=None):
        if self._refreshing:
            return
        self._refreshing = True
        if self.refresh_button is not None:
            self.refresh_button.disabled = True
        self.info.value = "Refreshing... <i class='fa fa-spinner fa-spin'></i>"

        def worker():
            try:
                self._do_refresh()
            except Exception as exc:
                self.show_error(f"Failed to refresh: {exc}")
            else:
                # Silent success: the "Last updated" timestamp below already
                # signals success, so no "refreshed" message is shown.
                self.info.value = ""
                if self._last_updated is not None:
                    self._last_updated.value = (
                        f"Last updated: {datetime.now().strftime('%H:%M:%S')}"  # noqa: DTZ005
                    )
            finally:
                # Re-enable the button before touching anything else: if a
                # later step raises, the page must not be left with the
                # button permanently disabled.
                self._refreshing = False
                if self.refresh_button is not None:
                    self.refresh_button.disabled = False

        threading.Thread(target=worker, daemon=True).start()

    def _do_refresh(self):
        """Synchronous probe/update; subclasses override.

        Runs on a background thread — avoid direct ipywidgets state
        mutation here except via the thread-safe show_*/info hooks.
        """


def _probe_daemon_workers(client) -> tuple[State, str, dict | None]:
    """Probe the daemon and return a (state, text, workers) tuple.

    `workers` maps worker PIDs to their circus stats, or is None if the
    daemon is not running.
    """
    try:
        # get_worker_info() blocks for the client timeout when the daemon is
        # down, so only call it after confirming that the daemon is running.
        if not client.is_daemon_running:
            return State.WARNING, "Daemon is not running", None
        workers = client.get_worker_info().get("info", {})
    except DaemonNotRunningException:
        # The daemon stopped between the check and the call.
        return State.WARNING, "Daemon is not running", None
    except Exception as exc:
        return State.ERROR, str(exc), None
    if not workers:
        # Nothing picks jobs off the queue, as if the daemon were not running.
        return State.WARNING, "Daemon is running with 0 workers", workers
    return State.OK, f"Daemon is running with {len(workers)} worker(s)", workers


def _humanize_age(seconds) -> str:
    """Humanize a duration in seconds, e.g. 100905.8 -> '1d 4h'."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError, OverflowError):
        return "?"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _format_percent(value) -> str:
    # circus reports "N/A" for values it could not read.
    return f"{value:.1f}%" if isinstance(value, (int, float)) else "?"


def _worker_table_html(workers) -> str:
    """Table of per-worker circus stats, or '' if there are no workers."""
    if not workers:
        return ""
    headers = ("Worker", "PID", "CPU", "Memory", "RSS", "Uptime")
    rows = [
        "".join(
            f"<th style='padding:1px 8px;text-align:left;'>{header}</th>"
            for header in headers
        )
    ]
    for pid, stats in workers.items():
        if not isinstance(stats, dict):
            # circus reports a message instead of stats for a worker that
            # has just stopped.
            stats = {}
        cells = (
            stats.get("wid", "?"),
            pid,
            _format_percent(stats.get("cpu")),
            _format_percent(stats.get("mem")),
            stats.get("mem_info1", "?"),
            _humanize_age(stats.get("age")),
        )
        rows.append(
            "".join(
                f"<td style='padding:1px 8px;'>{html.escape(str(cell))}</td>"
                for cell in cells
            )
        )
    return (
        "<table style='border-collapse:collapse;'>"
        + "".join(f"<tr>{row}</tr>" for row in rows)
        + "</table>"
    )


_LOG_TAIL_BYTES = 64 * 1024


def _read_log_tail(path, lines) -> str:
    """The last `lines` lines of the file at `path`.

    Reads at most the last 64 KiB, so a large log is never loaded whole.
    """
    try:
        with Path(path).open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - _LOG_TAIL_BYTES))
            data = handle.read()
    except FileNotFoundError:
        return "Log file not found"
    if not data:
        return "(log is empty)"
    return "\n".join(data.decode(errors="replace").splitlines()[-lines:])


class DaemonControlWidget(ControlSectionWidget):
    description = "The daemon runs your AiiDA processes in the background."
    _LOG_TAIL_LINES = 50

    def __init__(self):
        self._busy = False
        self._daemon = manage.get_manager().get_daemon_client()
        self._status = ipw.HTML()
        self._worker_table = ipw.HTML()

        self.start_button = ipw.Button(
            description="Start daemon", button_style="info", icon="play"
        )
        self.start_button.on_click(self._on_start)
        self.stop_button = ipw.Button(
            description="Stop daemon", button_style="danger", icon="stop"
        )
        self.stop_button.on_click(self._on_stop)
        self.restart_button = ipw.Button(
            description="Restart daemon", button_style="warning", icon="repeat"
        )
        self.restart_button.on_click(self._on_restart)
        self.add_worker_button = ipw.Button(description="Add worker", icon="plus")
        self.add_worker_button.on_click(self._on_add_worker)
        self.remove_worker_button = ipw.Button(
            description="Remove worker", icon="minus"
        )
        self.remove_worker_button.on_click(self._on_remove_worker)

        self._log_content = ipw.HTML()
        reload_log_button = ipw.Button(description="Reload log", icon="refresh")
        reload_log_button.on_click(self._reload_log)
        self._log_accordion = ipw.Accordion(
            children=[ipw.VBox([self._log_content, reload_log_button])],
            titles=[f"Daemon log ({Path(self._daemon.daemon_log_file).name})"],
            selected_index=None,
        )
        self._log_accordion.observe(self._reload_log_if_open, names="selected_index")

        buttons = [
            self.start_button,
            self.stop_button,
            self.restart_button,
            self.add_worker_button,
            self.remove_worker_button,
        ]
        super().__init__(
            [self._status, self._worker_table, ipw.HBox(buttons), self._log_accordion]
        )
        # super().__init__() creates the refresh button; it is disabled during
        # actions too, so that a refresh cannot run alongside one.
        self._action_buttons = list(buttons)
        if self.refresh_button is not None:
            self._action_buttons.append(self.refresh_button)

    def _on_start(self, _=None):
        if self._daemon.is_daemon_running:
            self.show_warning("The daemon is already running.")
            return
        self._run_action(
            "start the daemon",
            self._start,
            "Starting the daemon...",
            "The daemon has been started.",
        )

    def _on_stop(self, _=None):
        if not self._daemon.is_daemon_running:
            self.show_warning("The daemon is not running.")
            return
        self._run_action(
            "stop the daemon",
            self._daemon.stop_daemon,
            "Stopping the daemon...",
            "The daemon has been stopped.",
        )

    def _on_restart(self, _=None):
        self._run_action(
            "restart the daemon",
            self._restart_or_start,
            "Restarting the daemon...",
            "The daemon has been restarted.",
        )

    def _on_add_worker(self, _=None):
        if not self._daemon.is_daemon_running:
            self.show_warning("The daemon is not running.")
            return
        self._run_action(
            "add a worker",
            lambda: self._daemon.increase_workers(1),
            "Adding a worker...",
            "A worker has been added.",
        )

    def _on_remove_worker(self, _=None):
        if not self._daemon.is_daemon_running:
            self.show_warning("The daemon is not running.")
            return
        self._run_action(
            "remove a worker",
            self._remove_worker,
            "Removing a worker...",
            "A worker has been removed.",
        )

    def _start(self):
        # Like `verdi daemon start`, start the configured number of workers.
        self._daemon.start_daemon(
            number_workers=manage.get_config_option("daemon.default_workers")
        )

    def _restart_or_start(self):
        try:
            return self._daemon.restart_daemon()
        except DaemonNotRunningException:
            self._start()

    def _remove_worker(self):
        # The button state can be stale, so re-check the live worker count.
        if self._daemon.get_number_of_workers() <= 1:
            raise RuntimeError("at least one worker is required")
        return self._daemon.decrease_workers(1)

    def _run_action(self, action_name, action, in_progress_message, success_message):
        if self._busy:
            return
        self._busy = True
        for button in self._action_buttons:
            button.disabled = True
        self.info.value = f"{in_progress_message} <i class='fa fa-spinner fa-spin'></i>"

        def worker():
            try:
                response = action()
                # circus refuses a command that clashes with one still running
                # (e.g. a removed worker still shutting down) in its reply
                # instead of raising.
                if response and response.get("status") == "error":
                    raise RuntimeError(response["reason"])
            except Exception as exc:
                self.show_error(f"Failed to {action_name}: {exc}")
            else:
                self.show_success(success_message)
            finally:
                # Re-enable the buttons before re-probing the status, so that
                # a failing probe cannot leave them all disabled.
                self._busy = False
                for button in self._action_buttons:
                    button.disabled = False
                try:
                    self._update_status()
                except Exception as exc:
                    self.info.value += "<br>" + _state_span(
                        State.ERROR, f"Failed to refresh the status: {exc}"
                    )

        threading.Thread(target=worker, daemon=True).start()

    def refresh(self, _=None):
        # A refresh during an action (e.g. when the tab is revisited) would
        # re-enable buttons; the action re-probes the status when it is done.
        if not self._busy:
            super().refresh()

    def _do_refresh(self):
        self._update_status()

    def _update_status(self):
        state, text, workers = _probe_daemon_workers(self._daemon)
        status = _state_span(state, text)
        cpus = os.cpu_count() or 1
        if workers and len(workers) > cpus:
            status += " " + _state_span(
                State.WARNING, f"(more workers than the {cpus} available CPUs)"
            )
        self._status.value = status
        self._worker_table.value = _worker_table_html(workers)

        running = workers is not None
        self.add_worker_button.disabled = not running
        self.add_worker_button.tooltip = "" if running else "The daemon is not running"
        if not running:
            self.remove_worker_button.disabled = True
            self.remove_worker_button.tooltip = "The daemon is not running"
        elif len(workers) <= 1:
            self.remove_worker_button.disabled = True
            self.remove_worker_button.tooltip = "At least one worker is required"
        else:
            self.remove_worker_button.disabled = False
            self.remove_worker_button.tooltip = ""

        self._reload_log_if_open()

    def _reload_log_if_open(self, _=None):
        if self._log_accordion.selected_index is not None:
            self._reload_log()

    def _reload_log(self, _=None):
        try:
            tail = _read_log_tail(self._daemon.daemon_log_file, self._LOG_TAIL_LINES)
        except Exception as exc:
            self._log_content.value = _state_span(
                State.ERROR, f"Failed to read the log: {exc}"
            )
        else:
            self._log_content.value = (
                "<pre style='max-height:300px;overflow:auto;font-size:12px;'>"
                f"{html.escape(tail)}</pre>"
            )


def _storage_summary(profile) -> str | None:
    """Compact, credential-free one-line summary of a profile's storage.

    Returns None for a backend it doesn't recognize, leaving it to the
    caller to fall back to `str(storage)`.

    Never renders `profile.storage_config` raw — it contains
    `database_password`.
    """
    backend = profile.storage_backend
    try:
        if backend == "core.psql_dos":
            config = profile.storage_config
            return (
                f"PostgreSQL {config['database_name']} @ "
                f"{config['database_hostname']}:{config['database_port']} "
                "+ file repository"
            )
        if backend == "core.sqlite_dos":
            return "SQLite database + file repository"
    except KeyError:
        pass
    return None


def _current_default_profile_name() -> str | None:
    """Read the on-disk config's default profile name directly.

    Bypasses AiiDA's cached config object, which does not pick up a
    default changed by another process (e.g. `verdi profile setdefault`
    run in a terminal) for the lifetime of this kernel.
    """
    with open(manage.get_config().filepath) as handle:
        return json.load(handle).get("default_profile")


def _sanitize_broker_url(url) -> str:
    """Strip credentials (`user:pass@`) from a broker URL, e.g. an AMQP URL.

    Matches through the last `@` so a password containing `@` isn't
    partially leaked.
    """
    return re.sub(r"://[^/ ]+@", "://", url)


class AiidaStatusOverviewWidget(ControlSectionWidget):
    description = "Health of the services behind AiiDA."

    def __init__(self):
        self._daemon: _DaemonClient = manage.get_manager().get_daemon_client()
        self._status = ipw.HTML()
        super().__init__([self._status])

    @classmethod
    def _status_row(cls, state: State, label, text, tooltip=None):
        icon = state.icon
        color = state.color
        text_color = "inherit" if state == State.OK else color
        title_attr = f" title='{html.escape(tooltip)}'" if tooltip else ""
        return (
            f"<tr>"
            f"<td style='padding:1px 8px;'>"
            f"<span style='color:{color}'>{icon}</span></td>"
            f"<td style='padding:1px 8px;'><b>{html.escape(label)}</b></td>"
            f"<td style='color:{text_color};padding:1px 8px;'{title_attr}>"
            f"{html.escape(text)}</td>"
            f"</tr>"
        )

    def _get_aiida_version(self):
        return self._status_row(State.OK, "version", f"AiiDA v{aiida.__version__}")

    def _get_config_dir(self):
        return self._status_row(State.OK, "config", manage.get_config().dirpath)

    def _probe_profile(self):
        # Stashed for `_probe_storage`, which needs to know whether a
        # profile is loaded; reset at the top of every `_do_refresh`.
        if self._profile is None:
            return self._status_row(State.ERROR, "profile", "No profile loaded")

        try:
            default_name = _current_default_profile_name()
        except Exception:
            logger.exception("Status overview: could not read on-disk default profile")
            default_name = None

        if default_name is not None and default_name != self._profile.name:
            return self._status_row(
                State.WARNING,
                "profile",
                "Change of profile detected - reload the page to apply",
            )
        return self._status_row(State.OK, "profile", self._profile.name)

    def _probe_storage(self):
        if self._profile is None:
            return self._status_row(State.ERROR, "storage", "No profile loaded")
        # The storage object is cached, so it does not re-verify the
        # connection; run a cheap query to actually probe the database.
        orm.QueryBuilder().append(orm.User).count()
        storage = manage.get_manager().get_profile_storage()
        storage_text = str(storage)
        summary = _storage_summary(self._profile) or storage_text
        return self._status_row(State.OK, "storage", summary, tooltip=storage_text)

    def _probe_broker(self):
        broker = manage.get_manager().get_broker()
        if broker is None:
            return self._status_row(State.WARNING, "broker", "No broker configured")
        sanitized = _sanitize_broker_url(str(broker))
        return self._status_row(State.OK, "broker", sanitized, tooltip=sanitized)

    def _probe_daemon(self):
        """Fetch the daemon status and return a row for the status table."""
        client = self._daemon
        if not client.is_daemon_running:
            return self._status_row(State.WARNING, "daemon", "Daemon is not running")
        try:
            workers = client.get_number_of_workers()
        except DaemonException:
            # The daemon stopped between the check and the call.
            return self._status_row(State.WARNING, "daemon", "Daemon is not running")
        if workers == 0:
            # The supervisor process is up, but with no workers nothing
            # picks jobs off the queue — indistinguishable from not running.
            return self._status_row(
                State.WARNING, "daemon", "Daemon is running with 0 workers"
            )
        return self._status_row(
            State.OK, "daemon", f"Daemon is running with {workers} worker(s)"
        )

    def _run_probe(self, label, probe):
        """Run `probe()` and return its row, or an error row if it raises."""
        try:
            return probe()
        except Exception as exc:
            logger.exception("Status overview: %s probe failed", label)
            return self._status_row(State.ERROR, label, str(exc))

    def _do_refresh(self):
        try:
            self._profile = get_profile()
        except Exception:
            logger.exception("Status overview: profile probe failed")
            self._profile = None
        probes = (
            ("version", self._get_aiida_version),
            ("config", self._get_config_dir),
            ("profile", self._probe_profile),
            ("storage", self._probe_storage),
            ("broker", self._probe_broker),
            ("daemon", self._probe_daemon),
        )
        rows = [self._run_probe(label, probe) for label, probe in probes]
        self._status.value = (
            "<table style='border-collapse:collapse;'>" + "".join(rows) + "</table>"
        )


_CGROUP_DIR = Path("/sys/fs/cgroup")  # module constant so tests can monkeypatch


def _read_cgroup_quantity(filename) -> int | None:
    """Value of a cgroup v2 file, or None if missing or 'max' (i.e. unlimited)."""
    try:
        text = (_CGROUP_DIR / filename).read_text().strip()
    except OSError:
        return None
    if text == "max":
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _format_bytes(n) -> str:
    """Human-readable binary size, e.g. '3.4 GiB'."""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


_MEMINFO_PATH = Path("/proc/meminfo")  # module constant so tests can monkeypatch


def _memory_status() -> tuple[int, int]:
    """The RAM memory usage of the container.

    Returns a tuple of (used_bytes, total_bytes).

    It distinguishes two cases:

    - Limited per container: cgroup-scoped, memory.current - inactive_file
    (docker stats convention) against memory.max.

    - Unlimited: host-wide instead, since nothing bounds this container but
    the shared pool - MemTotal - MemAvailable against MemTotal, from
    /proc/meminfo.

    Raises if memory.current/memory.stat are missing or malformed, which
    shouldn't happen in AiiDAlab's Docker deployment.
    """
    current = _read_cgroup_quantity("memory.current")
    if current is None:
        raise RuntimeError("cgroup v2 memory accounting unavailable")

    limit = _read_cgroup_quantity("memory.max")
    if limit is None:
        # No per-container limit: fall back to host-wide memory accounting.
        meminfo = {}
        for line in _MEMINFO_PATH.read_text().splitlines():
            key, _, value = line.partition(":")
            if value:
                meminfo[key] = int(value.split()[0]) * 1024
        total = meminfo["MemTotal"]
        return total - meminfo["MemAvailable"], total

    try:
        stat = dict(
            line.split()
            for line in (_CGROUP_DIR / "memory.stat").read_text().splitlines()
        )
        used = current - int(stat["inactive_file"])
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError("cgroup v2 memory.stat unavailable") from exc

    return used, limit


def _cpu_status() -> tuple[float, float]:
    """The CPU usage of the container.

    Returns a tuple of (load_1min, effective_cpus).

    load_1min is host-wide - no cgroup equivalent exists. effective_cpus
    is the cpu.max quota/period when set, else os.cpu_count().

    Raises if cpu.max is missing or malformed, which shouldn't happen in
    AiiDAlab's Docker deployment.
    """
    load_1min = os.getloadavg()[0]

    try:
        quota_str, period_str = (_CGROUP_DIR / "cpu.max").read_text().split()
    except OSError as exc:
        raise RuntimeError("cgroup v2 cpu.max unavailable") from exc

    if quota_str == "max":
        return load_1min, float(os.cpu_count() or 1)

    try:
        return load_1min, int(quota_str) / int(period_str)
    except (ValueError, ZeroDivisionError) as exc:
        raise RuntimeError("cgroup v2 cpu.max unavailable") from exc


def _disk_status() -> tuple[int, int]:
    """The disk usage of the filesystem hosting Path.home().

    Returns a tuple of (used_bytes, total_bytes)."""
    usage = shutil.disk_usage(Path.home())
    return usage.used, usage.total


def _safe_fraction(used, total) -> float:
    """used / total, or raise if total is falsy (row is then unavailable)."""
    if not total:
        raise ValueError("total value unavailable")
    return used / total


class SystemResourcesWidget(ControlSectionWidget):
    description = "Memory, CPU and disk usage of this container."
    _THRESHOLDS: ClassVar[tuple] = ((0.75, "success"), (0.90, "warning"))
    _LABEL_WIDTH = "90px"

    def _bar_row(self, label):
        bar = ipw.FloatProgress(min=0, max=1)
        text = ipw.HTML()
        row = ipw.HBox(
            [
                ipw.HTML(f"<b>{label}</b>", layout=ipw.Layout(width=self._LABEL_WIDTH)),
                bar,
                text,
            ]
        )
        return bar, text, row

    def __init__(self):
        self._memory_bar, self._memory_label, memory_row = self._bar_row("Memory")
        self._cpu_bar, self._cpu_label, cpu_row = self._bar_row("CPU load")
        self._disk_bar, self._disk_label, disk_row = self._bar_row("Disk")

        super().__init__([memory_row, cpu_row, disk_row])

    @classmethod
    def _bar_style(cls, fraction):
        for threshold, style in cls._THRESHOLDS:
            if fraction < threshold:
                return style
        return "danger"

    def _set_row(self, bar, label, fraction, text):
        bar.value = max(0.0, min(1.0, fraction))
        bar.bar_style = self._bar_style(bar.value)
        label.value = text

    def _set_row_error(self, bar, label, exc):
        bar.value = 0
        bar.bar_style = "danger"
        label.value = _state_span(State.ERROR, str(exc))

    def _do_refresh(self):
        try:
            used, total = _memory_status()
            fraction = _safe_fraction(used, total)
            text = f"{_format_bytes(used)} / {_format_bytes(total)} ({fraction:.0%})"
            self._set_row(self._memory_bar, self._memory_label, fraction, text)
        except Exception as exc:
            self._set_row_error(self._memory_bar, self._memory_label, exc)

        try:
            load_1min, cpus = _cpu_status()
            fraction = _safe_fraction(load_1min, cpus)
            text = f"load {load_1min:.2f} / {cpus:.2f} CPUs ({fraction:.0%})"
            self._set_row(self._cpu_bar, self._cpu_label, fraction, text)
        except Exception as exc:
            self._set_row_error(self._cpu_bar, self._cpu_label, exc)

        try:
            used, total = _disk_status()
            fraction = _safe_fraction(used, total)
            text = (
                f"{_format_bytes(used)} / {_format_bytes(total)} "
                f"({fraction:.0%}) — {Path.home()}"
            )
            self._set_row(self._disk_bar, self._disk_label, fraction, text)
        except Exception as exc:
            self._set_row_error(self._disk_bar, self._disk_label, exc)


def _repository_path(profile) -> Path:
    """Directory holding the profile's file repository.

    For core.sqlite_dos this directory also holds the database file.
    """
    backend = profile.storage_backend
    if backend == "core.psql_dos":
        # Written by AiiDA with Path.as_uri(), i.e. percent-encoded.
        uri = profile.storage_config["repository_uri"]
        return Path(unquote(urlparse(uri).path))
    if backend == "core.sqlite_dos":
        return Path(profile.storage_config["filepath"])
    raise ValueError(f"not available for backend {backend}")


_DU_TIMEOUT = 120  # seconds


def _du_bytes(path) -> int:
    """Disk space used by `path` in bytes, computed with `du`.

    Counts allocated blocks, not apparent size: packing frees whole blocks while the
    apparent size stays put or even grows, so only blocks show what maintenance frees.

    `du` is much faster than walking the tree in Python on a disk-objectstore
    repository, which can hold very many small files. It exits nonzero when
    a file vanishes mid-scan (e.g. during maintenance), so its numeric output
    is trusted regardless of the exit code.
    """
    if not Path(path).exists():
        raise FileNotFoundError(f"{path} does not exist")
    result = subprocess.run(
        ["du", "-s", "--block-size=1", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=_DU_TIMEOUT,
    )
    try:
        return int(result.stdout.split()[0])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"could not determine the size of {path}") from exc


def _repository_size_or_none(profile) -> int | None:
    """Best-effort repository size, for reporting the space maintenance reclaimed."""
    try:
        return _du_bytes(_repository_path(profile))
    except Exception:
        return None


def _database_size_bytes(storage) -> int:
    """Size of the PostgreSQL database in bytes (core.psql_dos only)."""
    # Deliberately not closed: this is AiiDA's own thread-local session.
    session = storage.get_session()
    query = sa.text("SELECT pg_database_size(current_database())")
    return session.execute(query).scalar_one()


class _ListLogHandler(logging.Handler):
    """Collects log messages, so they can be shown to the user."""

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


class StorageWidget(ControlSectionWidget):
    description = "Disk usage of profile data and apps; storage maintenance."

    def __init__(self):
        self._maintaining = False
        self._daemon: _DaemonClient = manage.get_manager().get_daemon_client()
        self._table = ipw.HTML()

        self._dry_run_checkbox = ipw.Checkbox(
            value=True,
            description="Dry run (only report what would be done)",
            indent=False,
            layout=ipw.Layout(width="auto"),
        )
        self._full_checkbox = ipw.Checkbox(
            value=False,
            description=(
                "Full maintenance (needs exclusive access: "
                "stop the daemon and close other AiiDAlab apps)"
            ),
            indent=False,
            layout=ipw.Layout(width="auto"),
        )
        self._maintain_button = ipw.Button(
            description="Run maintenance", button_style="warning", icon="wrench"
        )
        self._maintain_button.on_click(self._on_maintain_clicked)
        self._maintain_output = ipw.HTML()

        super().__init__(
            [
                self._table,
                ipw.HTML("<h4>Maintenance</h4>"),
                self._dry_run_checkbox,
                self._full_checkbox,
                self._maintain_button,
                self._maintain_output,
            ]
        )

    @staticmethod
    def _row(label, usage, *args):
        """Table row showing `usage(*args)`, or its error if it raises."""
        try:
            text, color = usage(*args), "inherit"
        except Exception as exc:
            text, color = str(exc), State.ERROR.color
        return (
            "<tr>"
            f"<td style='padding:1px 8px;'><b>{html.escape(label)}</b></td>"
            f"<td style='color:{color};padding:1px 8px;'>{html.escape(text)}</td>"
            "</tr>"
        )

    @staticmethod
    def _repository_usage(profile):
        path = _repository_path(profile)
        return f"{_format_bytes(_du_bytes(path))} — {path}"

    @staticmethod
    def _database_usage(profile):
        backend = profile.storage_backend
        if backend == "core.sqlite_dos":
            return "included in the file repository"
        if backend == "core.psql_dos":
            storage = manage.get_manager().get_profile_storage()
            return _format_bytes(_database_size_bytes(storage))
        raise ValueError(f"not available for backend {backend}")

    @staticmethod
    def _apps_usage():
        return f"{_format_bytes(_du_bytes(AIIDALAB_APPS))} — {AIIDALAB_APPS}"

    @staticmethod
    def _home_usage():
        used, total = _disk_status()
        return f"{_format_bytes(used)} used of {_format_bytes(total)} — {Path.home()}"

    def _do_refresh(self):
        profile = get_profile()
        if profile is None:
            raise RuntimeError("no AiiDA profile is loaded")
        rows = (
            self._row("Profile", lambda: profile.name),
            self._row("File repository", self._repository_usage, profile),
            self._row("Database", self._database_usage, profile),
            self._row("Installed apps", self._apps_usage),
            self._row("Home filesystem", self._home_usage),
        )
        self._table.value = (
            "<table style='border-collapse:collapse;'>" + "".join(rows) + "</table>"
        )

    def refresh(self, _=None):
        # Don't measure the repository while maintenance rewrites it (e.g. when
        # the tab is revisited); the maintenance run refreshes when it is done.
        if not self._maintaining:
            super().refresh()

    def _set_controls_disabled(self, disabled):
        self._dry_run_checkbox.disabled = disabled
        self._full_checkbox.disabled = disabled
        self._maintain_button.disabled = disabled
        if self.refresh_button is not None:
            self.refresh_button.disabled = disabled

    def _on_maintain_clicked(self, _=None):
        if self._maintaining:
            return
        self._maintaining = True
        self._set_controls_disabled(True)
        self._maintain_output.value = (
            "Running maintenance... <i class='fa fa-spinner fa-spin'></i>"
        )
        full = self._full_checkbox.value
        dry_run = self._dry_run_checkbox.value

        def worker():
            try:
                self._maintain_output.value = self._maintain(full, dry_run)
            except Exception as exc:
                self._maintain_output.value = _state_span(
                    State.ERROR, f"Maintenance failed: {exc}"
                )
            finally:
                # Re-enable the controls before refreshing the table, so that
                # a failing refresh cannot leave them disabled.
                self._maintaining = False
                self._set_controls_disabled(False)
                if not dry_run:
                    self.refresh()

        threading.Thread(target=worker, daemon=True).start()

    def _maintain(self, full, dry_run):
        """Run the storage maintenance and return the report as HTML."""
        # A friendly pre-check: full maintenance locks the profile, which
        # fails anyway while the daemon is using it.
        if full and self._daemon.is_daemon_running:
            return _state_span(
                State.WARNING,
                "Full maintenance needs exclusive access to the profile. "
                "Stop the daemon first (Daemon tab) and close other AiiDAlab apps.",
            )

        profile = get_profile()
        storage = manage.get_manager().get_profile_storage()
        before = None if dry_run else _repository_size_or_none(profile)

        # Maintenance reports its progress at INFO level, below AiiDA's default.
        handler = _ListLogHandler()
        previous_level = STORAGE_LOGGER.level
        STORAGE_LOGGER.setLevel(logging.INFO)
        STORAGE_LOGGER.addHandler(handler)
        try:
            storage.maintain(full=full, dry_run=dry_run)
        finally:
            STORAGE_LOGGER.removeHandler(handler)
            STORAGE_LOGGER.setLevel(previous_level)

        lines = [html.escape(line) for line in handler.lines] or ["(no log output)"]
        if before is not None:
            after = _repository_size_or_none(profile)
            if after is not None:
                reclaimed = _format_bytes(max(before - after, 0))
                lines.append(f"<b>Reclaimed {reclaimed} of repository space.</b>")
        summary = "Dry run finished." if dry_run else "Maintenance finished."
        return _state_span(State.OK, summary) + "<br>" + "<br>".join(lines)


_PROCESS_ACTION_TIMEOUT = 5.0  # seconds to wait for the processes to respond


class ProcessControlWidget(ControlSectionWidget):
    description = "Inspect, pause, resume or kill AiiDA processes."

    def __init__(self):
        self._busy = False
        self._kill_armed = False
        self._daemon = manage.get_manager().get_daemon_client()

        self._state_filter = ipw.SelectMultiple(
            options=[state.value for state in ProcessState],
            value=("running", "waiting"),
            rows=len(ProcessState),
            description="Process state:",
            style={"description_width": "initial"},
        )
        self._past_days = ipw.IntText(value=7, description="Past days:")
        self._all_days = ipw.Checkbox(value=True, description="All days")
        self.process_list = ProcessListWidget(
            path_to_root="../",
            process_states=list(self._state_filter.value),
            past_days=-1,
        )
        tl.dlink(
            (self._state_filter, "value"),
            (self.process_list, "process_states"),
            transform=list,
        )
        # Both controls feed `past_days` one way: linking the day count back
        # would let "All days" overwrite it with -1.
        tl.dlink((self._all_days, "value"), (self._past_days, "disabled"))
        self._past_days.observe(self._update_past_days, names="value")
        self._all_days.observe(self._update_past_days, names="value")

        self._selection = ipw.SelectMultiple(
            description="Act on:",
            rows=8,
            layout=ipw.Layout(width="600px"),
            style={"description_width": "initial"},
        )
        self._selection.observe(self._on_selection_change, names="value")
        self.process_list.observe(self._on_list_updated, names="updated")

        self.pause_button = ipw.Button(description="Pause", icon="pause", disabled=True)
        self.pause_button.on_click(self._on_pause)
        self.play_button = ipw.Button(description="Play", icon="play", disabled=True)
        self.play_button.on_click(self._on_play)
        self.kill_button = ipw.Button(
            description="Kill", icon="times", button_style="danger", disabled=True
        )
        self.kill_button.on_click(self._on_kill)
        self._action_status = ipw.HTML()

        super().__init__(
            [
                ipw.HBox(
                    [self._state_filter, ipw.VBox([self._past_days, self._all_days])]
                ),
                self.process_list,
                ipw.HTML("<h4>Actions</h4>"),
                self._selection,
                ipw.HBox([self.pause_button, self.play_button, self.kill_button]),
                self._action_status,
            ]
        )

    def _do_refresh(self):
        self.process_list.update()

    def _update_past_days(self, _=None):
        self.process_list.past_days = (
            -1 if self._all_days.value else self._past_days.value
        )

    def _on_list_updated(self, _=None):
        self._rebuild_options()
        self._disarm_kill()
        self._sync_buttons()

    def _rebuild_options(self):
        """List the displayed processes, keeping the selected ones selected."""
        previous_selection = set(self._selection.value)
        options = []
        for row in self.process_list.current_rows:
            pk = int(row[HEADER_PK])
            label = f"{pk} | {row[HEADER_PROCESS_LABEL]} | {row[HEADER_STATE]}"
            options.append((label, pk))
        self._selection.options = options
        self._selection.value = tuple(
            pk for _, pk in options if pk in previous_selection
        )

    def _on_selection_change(self, _=None):
        self._disarm_kill()
        self._sync_buttons()

    def _sync_buttons(self):
        disabled = self._busy or not self._selection.value
        for button in (self.pause_button, self.play_button, self.kill_button):
            button.disabled = disabled

    def _disarm_kill(self):
        self._kill_armed = False
        self.kill_button.description = "Kill"

    def _on_pause(self, _=None):
        self._disarm_kill()
        self._run_action("pause", process_control.pause_processes)

    def _on_play(self, _=None):
        self._disarm_kill()
        self._run_action("play", process_control.play_processes)

    def _on_kill(self, _=None):
        if not self._kill_armed:
            self._kill_armed = True
            self.kill_button.description = (
                f"Confirm kill ({len(self._selection.value)})"
            )
            return
        self._disarm_kill()
        self._run_action("kill", process_control.kill_processes)

    def _run_action(self, verb, control_function):
        pks = list(self._selection.value)
        if self._busy or not pks:
            return
        if not self._daemon.is_daemon_running:
            self._action_status.value = _state_span(
                State.WARNING,
                "Process actions need a running daemon: start it in the Daemon tab.",
            )
            return

        self._busy = True
        self._sync_buttons()
        self._action_status.value = (
            f"Sending the {verb} request... <i class='fa fa-spinner fa-spin'></i>"
        )

        def worker():
            try:
                self._action_status.value = self._act(verb, control_function, pks)
            except Exception as exc:
                self._action_status.value = _state_span(
                    State.ERROR, f"Failed to {verb} the process(es): {exc}"
                )
            finally:
                # Re-enable the buttons before updating the list, so that a
                # failing update cannot leave them disabled.
                self._busy = False
                self._sync_buttons()
                self.process_list.update()

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _act(verb, control_function, pks):
        """Send the request to the processes and return the report as HTML."""
        # Load the nodes in this thread: AiiDA ORM objects must not be shared
        # between threads.
        nodes, errors = [], []
        for pk in pks:
            try:
                nodes.append(orm.load_node(pk))
            except NotExistent as exc:
                errors.append(f"PK {pk}: {exc}")

        # The outcome for each process (e.g. already terminated, unreachable,
        # timed out) is only logged, so capture the log to show it.
        handler = _ListLogHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        process_control.LOGGER.addHandler(handler)
        try:
            if nodes:
                control_function(nodes, timeout=_PROCESS_ACTION_TIMEOUT)
        finally:
            process_control.LOGGER.removeHandler(handler)

        summary = (
            f"{verb.capitalize()} requested for {len(nodes)} process(es). "
            "Their states may take a few seconds to change."
        )
        report = "<br>".join(html.escape(line) for line in (summary, *handler.lines))
        if errors:
            report += "<br>" + _state_span(State.ERROR, "; ".join(errors))
        return report


class ProfileControlWidget(ControlSectionWidget):
    description = "Manage AiiDA profiles: default profile and deletion."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])


class DangerZoneWidget(ControlSectionWidget):
    description = "Irreversible actions that can lead to data loss."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])
