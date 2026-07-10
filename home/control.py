import html
import logging
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import aiida
import ipywidgets as ipw
import plumpy
import psutil
import traitlets as tr
from aiida import engine, get_profile, manage, orm
from aiida.common.exceptions import NotExistent
from aiida.engine.daemon.client import DaemonException
from aiida.engine.processes import control as process_control
from sqlalchemy import text

from home import process
from home.themes import ThemeDefault as Theme

_STATE_COLORS = {
    "ok": Theme.COLORS.CHECK,
    "warning": Theme.COLORS.AIIDALAB_ORANGE,
    "error": Theme.COLORS.DANGER,
}


def _state_span(state, text) -> str:
    return f"<span style='color:{_STATE_COLORS[state]}'>{html.escape(text)}</span>"


_FACTORY_RESET_FILE = (
    Path.home() / "AIIDALAB_FACTORY_RESET"
)  # module constant so tests can monkeypatch


class ControlSectionWidget(ipw.VBox):
    """Shared anatomy for a control-page tab: description, body, footer.

    The footer (refresh button, "Last updated" label, transient feedback) and
    the threaded refresh-with-guard pattern are common to every section, so
    they live here; subclasses only provide a body and a `_do_refresh()`.
    """

    description = ""

    def __init__(self, body_children):
        self._refreshing = False

        header_children = []
        if self.description:
            header_children.append(
                ipw.HTML(
                    f"<div style='color:{Theme.COLORS.GRAY};font-size:13px;"
                    f"margin:2px 0 6px 0;'>{html.escape(self.description)}</div>"
                )
            )

        self.refresh_button = ipw.Button(description="Refresh", icon="refresh")
        self.refresh_button.on_click(self.refresh)
        self._last_updated = ipw.HTML()
        self.info = ipw.HTML()
        footer = ipw.HBox([self.refresh_button, self._last_updated, self.info])

        super().__init__(
            children=[*header_children, *body_children, footer],
        )
        self.layout.padding = "8px 0 0 0"

    def show_success(self, text):
        self.info.value = _state_span("ok", text)

    def show_warning(self, text):
        self.info.value = _state_span("warning", text)

    def show_error(self, text):
        self.info.value = _state_span("error", text)

    def show_plain(self, text):
        self.info.value = html.escape(text)

    def refresh(self, _=None):
        if self._refreshing:
            return
        self._refreshing = True
        self.info.value = "Refreshing... <i class='fa fa-spinner fa-spin'></i>"
        self.refresh_button.disabled = True

        def worker():
            try:
                self._do_refresh()
            except Exception as exc:
                self.show_error(f"Failed to refresh: {exc}")
            else:
                # Silent success: the "Last updated" timestamp below already
                # signals success, so no "refreshed" message is shown.
                self.info.value = ""
            finally:
                # Re-enable the button before touching anything else: if a
                # later step raises, the page must not be left with the
                # button permanently disabled.
                self.refresh_button.disabled = False
                self._refreshing = False
                self._last_updated.value = (
                    f"Last updated: {datetime.now().strftime('%H:%M:%S')}"
                )

        threading.Thread(target=worker, daemon=True).start()

    def _do_refresh(self):
        """Synchronous probe/update; subclasses override."""


def _probe_daemon(client) -> tuple:
    """Probe the daemon and return an (state, text, info) tuple.

    `info` is the raw `get_worker_info()` return value, or None if the
    daemon is not running (or it stopped between the check and the call).
    """
    try:
        state, text, info = "warning", "Daemon is not running", None
        # get_worker_info() blocks for the client timeout when the daemon
        # is down, so only call it after confirming the daemon is running.
        if client.is_daemon_running:
            try:
                info = client.get_worker_info()
                workers = len(info.get("info", {}))
                state, text = "ok", f"Daemon is running with {workers} worker(s)"
            except DaemonException:
                info = None  # The daemon stopped between the check and the call.
    except Exception as exc:
        return "error", str(exc), None
    else:
        return state, text, info


def _daemon_status(client) -> tuple:
    """Probe the daemon and return an (state, text) status pair."""
    state, text, _info = _probe_daemon(client)
    return state, text


def _storage_summary(profile, storage) -> str:
    """Compact, credential-free one-line summary of a profile's storage.

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
    return str(storage)


def _sanitize_broker_url(url) -> str:
    """Strip userinfo (`user:pass@`) from a broker URL, e.g. an AMQP URL."""
    return re.sub(r"://[^@/ ]+@", "://", url)


def _humanize_age(seconds) -> str:
    """Humanize a duration in seconds, e.g. 100905.8 -> '1d 4h'."""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "?"
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _worker_table_html(info) -> str:
    """Table of per-worker stats from `get_worker_info()`, or '' if none."""
    workers = info.get("info") if info else None
    if not workers:
        return ""

    def _fmt_pct(value):
        return f"{value:.1f}%" if isinstance(value, (int, float)) else "?"

    header = (
        "<tr>"
        "<th style='padding:1px 8px;text-align:left;'>Worker</th>"
        "<th style='padding:1px 8px;text-align:left;'>PID</th>"
        "<th style='padding:1px 8px;text-align:left;'>CPU</th>"
        "<th style='padding:1px 8px;text-align:left;'>Memory</th>"
        "<th style='padding:1px 8px;text-align:left;'>RSS</th>"
        "<th style='padding:1px 8px;text-align:left;'>Uptime</th>"
        "</tr>"
    )
    rows = []
    for worker in workers.values():
        cells = [
            str(worker.get("wid", "?")),
            str(worker.get("pid", "?")),
            _fmt_pct(worker.get("cpu")),
            _fmt_pct(worker.get("mem")),
            str(worker.get("mem_info1", "?")),
            _humanize_age(worker.get("age")),
        ]
        rows.append(
            "<tr>"
            + "".join(
                f"<td style='padding:1px 8px;'>{html.escape(cell)}</td>"
                for cell in cells
            )
            + "</tr>"
        )
    return (
        "<table style='border-collapse:collapse;'>"
        + header
        + "".join(rows)
        + "</table>"
    )


def _read_log_tail(path, lines=50) -> str:
    """Last `lines` lines of the file at `path`, without reading it whole."""
    log_path = Path(path)
    if not log_path.exists():
        return "Log file not found"
    size = log_path.stat().st_size
    if size == 0:
        return "(log is empty)"
    with log_path.open("rb") as f:
        f.seek(max(0, size - 65536))
        data = f.read()
    return "\n".join(data.decode(errors="replace").splitlines()[-lines:])


class DaemonControlWidget(ControlSectionWidget):
    description = "The daemon runs your AiiDA processes in the background."
    _LOG_TAIL_LINES = 50

    def __init__(self):
        self._daemon = engine.daemon.get_daemon_client()
        self._status = ipw.HTML()
        self._worker_table = ipw.HTML()

        # Start daemon.
        self.start_button = ipw.Button(
            description="Start daemon", button_style="info", icon="play"
        )
        self.start_button.on_click(self._start_daemon)

        # Stop daemon.
        self.stop_button = ipw.Button(
            description="Stop daemon", button_style="danger", icon="stop"
        )
        self.stop_button.on_click(self._stop_daemon)

        # Restart daemon.
        self.restart_button = ipw.Button(
            description="Restart daemon", button_style="warning", icon="repeat"
        )
        self.restart_button.on_click(self._restart_daemon)

        # Add/remove workers.
        self.add_worker_button = ipw.Button(description="Add worker", icon="plus")
        self.add_worker_button.on_click(self._add_worker)
        self.remove_worker_button = ipw.Button(
            description="Remove worker", icon="minus"
        )
        self.remove_worker_button.on_click(self._remove_worker)

        # Daemon log viewer.
        self._log_content = ipw.HTML()
        self.reload_log_button = ipw.Button(description="Reload log", icon="refresh")
        self.reload_log_button.on_click(self._reload_log)
        self._log_accordion = ipw.Accordion(
            children=[ipw.VBox([self._log_content, self.reload_log_button])],
            selected_index=None,
        )
        self._log_accordion.set_title(
            0, f"Daemon log ({Path(self._daemon.daemon_log_file).name})"
        )

        super().__init__(
            body_children=[
                self._status,
                self._worker_table,
                ipw.HBox(
                    [
                        self.start_button,
                        self.stop_button,
                        self.restart_button,
                        self.add_worker_button,
                        self.remove_worker_button,
                    ]
                ),
                self._log_accordion,
            ]
        )

        # The refresh button also disables while an action runs.
        self._action_buttons = [
            self.start_button,
            self.stop_button,
            self.restart_button,
            self.add_worker_button,
            self.remove_worker_button,
            self.refresh_button,
        ]

        self._update_status()

    def _start_daemon(self, _=None):
        if self._daemon.is_daemon_running:
            self.show_warning("The daemon is already running.")
            return
        self._run_action(
            action_name="start the daemon",
            action=self._daemon.start_daemon,
            in_progress_message="Starting the daemon...",
            success_message="The daemon has been started.",
        )

    def _stop_daemon(self, _=None):
        if not self._daemon.is_daemon_running:
            self.show_warning("The daemon is not running.")
            return
        self._run_action(
            action_name="stop the daemon",
            action=self._daemon.stop_daemon,
            in_progress_message="Stopping the daemon...",
            success_message="The daemon has been stopped.",
        )

    def _restart_daemon(self, _=None):
        self._run_action(
            action_name="restart the daemon",
            action=self._restart_with_fallback,
            in_progress_message="Restarting the daemon...",
            success_message="The daemon has been restarted.",
        )

    def _add_worker(self, _=None):
        if not self._daemon.is_daemon_running:
            self.show_warning("The daemon is not running.")
            return
        self._run_action(
            action_name="add a worker",
            action=lambda: self._daemon.increase_workers(1),
            in_progress_message="Adding a worker...",
            success_message="A worker has been added.",
        )

    def _remove_worker(self, _=None):
        if not self._daemon.is_daemon_running:
            self.show_warning("The daemon is not running.")
            return
        # The button's disabled state can be stale, so re-check the live
        # worker count before actually removing one.
        try:
            worker_count = self._daemon.get_number_of_workers()
        except DaemonException:
            worker_count = 0
        if worker_count <= 1:
            self.show_warning("At least one worker is required.")
            return
        self._run_action(
            action_name="remove a worker",
            action=lambda: self._daemon.decrease_workers(1),
            in_progress_message="Removing a worker...",
            success_message="A worker has been removed.",
        )

    def _do_refresh(self):
        self._update_status()

    def _restart_with_fallback(self):
        # restart_daemon() raises DaemonNotRunningException if the daemon is
        # currently stopped, so fall back to starting it in that case.
        try:
            self._daemon.restart_daemon()
        except DaemonException:
            if self._daemon.is_daemon_running:
                raise
            self._daemon.start_daemon()

    def _run_action(self, action_name, action, in_progress_message, success_message):
        self.info.value = f"{in_progress_message} <i class='fa fa-spinner fa-spin'></i>"
        for button in self._action_buttons:
            button.disabled = True

        def worker():
            try:
                action()
            except Exception as exc:
                self.show_error(f"Failed to {action_name}: {exc}")
            else:
                self.show_success(success_message)
            finally:
                # Re-enable the buttons before refreshing the status: if the
                # refresh raises, the page must not be left with all buttons
                # permanently disabled.
                for button in self._action_buttons:
                    button.disabled = False
                try:
                    self._update_status()
                except Exception as exc:
                    self.info.value += "<br>" + _state_span(
                        "error", f"Failed to refresh the status: {exc}"
                    )

        threading.Thread(target=worker, daemon=True).start()

    def _reload_log(self, _=None):
        try:
            tail = _read_log_tail(self._daemon.daemon_log_file, self._LOG_TAIL_LINES)
            self._log_content.value = (
                "<pre style='max-height:300px;overflow:auto;font-size:12px;'>"
                f"{html.escape(tail)}</pre>"
            )
        except Exception as exc:
            self._log_content.value = _state_span(
                "error", f"Failed to read the log: {exc}"
            )

    def _update_status(self, _=None):
        state, text, info = _probe_daemon(self._daemon)
        status_html = _state_span(state, text)

        running = state == "ok"
        worker_count = len(info.get("info", {})) if info else 0
        cpu_count = os.cpu_count() or 1
        if running and worker_count > cpu_count:
            status_html += " " + _state_span(
                "warning", f"(exceeds {cpu_count} available CPU(s))"
            )
        self._status.value = status_html

        self.add_worker_button.disabled = not running
        self.add_worker_button.tooltip = "" if running else "The daemon is not running"
        if not running:
            self.remove_worker_button.disabled = True
            self.remove_worker_button.tooltip = "The daemon is not running"
        elif worker_count <= 1:
            self.remove_worker_button.disabled = True
            self.remove_worker_button.tooltip = "At least one worker is required"
        else:
            self.remove_worker_button.disabled = False
            self.remove_worker_button.tooltip = ""

        self._worker_table.value = _worker_table_html(info)

        if self._log_accordion.selected_index == 0:
            self._reload_log()


class StatusOverviewWidget(ControlSectionWidget):
    description = "Health of the services behind AiiDA."
    _ROW_ICONS: ClassVar[dict] = {
        "ok": Theme.ICONS.CHECK,
        "warning": Theme.ICONS.WARNING,
        "error": Theme.ICONS.TIMES_CIRCLE,
    }

    def __init__(self):
        self._daemon = engine.daemon.get_daemon_client()
        self._status = ipw.HTML()

        super().__init__(body_children=[self._status])
        self.refresh()

    @classmethod
    def _status_row(cls, state, label, text, tooltip=None):
        icon = cls._ROW_ICONS[state]
        color = _STATE_COLORS[state]
        text_color = "inherit" if state == "ok" else color
        title_attr = f" title='{html.escape(tooltip)}'" if tooltip else ""
        return (
            f"<tr>"
            f"<td style='padding:1px 8px;'>"
            f"<span style='color:{color}'>{icon}</span></td>"
            f"<td style='padding:1px 8px;'><b>{label}</b></td>"
            f"<td style='color:{text_color};padding:1px 8px;'{title_attr}>"
            f"{html.escape(text)}</td>"
            f"</tr>"
        )

    def _do_refresh(self):
        rows = []

        try:
            rows.append(
                self._status_row("ok", "version", f"AiiDA v{aiida.__version__}")
            )
        except Exception as exc:
            rows.append(self._status_row("error", "version", str(exc)))

        try:
            rows.append(self._status_row("ok", "config", manage.get_config().dirpath))
        except Exception as exc:
            rows.append(self._status_row("error", "config", str(exc)))

        try:
            profile = get_profile()
            if profile is None:
                rows.append(self._status_row("error", "profile", "No profile loaded"))
            else:
                rows.append(self._status_row("ok", "profile", profile.name))
        except Exception as exc:
            rows.append(self._status_row("error", "profile", str(exc)))

        try:
            # The storage object is cached, so it does not re-verify the
            # connection; run a cheap query to actually probe the database.
            orm.QueryBuilder().append(orm.User).count()
            storage = manage.get_manager().get_profile_storage()
            summary = _storage_summary(get_profile(), storage)
            rows.append(
                self._status_row("ok", "storage", summary, tooltip=str(storage))
            )
        except Exception as exc:
            rows.append(self._status_row("error", "storage", str(exc)))

        try:
            broker = manage.get_manager().get_broker()
            if broker is None:
                rows.append(
                    self._status_row("warning", "broker", "No broker configured")
                )
            else:
                sanitized = _sanitize_broker_url(str(broker))
                rows.append(
                    self._status_row("ok", "broker", sanitized, tooltip=sanitized)
                )
        except Exception as exc:
            rows.append(self._status_row("error", "broker", str(exc)))

        state, text = _daemon_status(self._daemon)
        rows.append(self._status_row(state, "daemon", text))

        self._status.value = (
            "<table style='border-collapse:collapse;'>" + "".join(rows) + "</table>"
        )


_CGROUP_DIR = Path("/sys/fs/cgroup")  # module constant so tests can monkeypatch


def _read_cgroup_int(filename) -> int | None:
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


def _memory_status() -> tuple[int, int, bool]:
    """(used_bytes, total_bytes, limited).

    used = memory.current - inactive_file (docker stats convention; falls back
    to memory.current if memory.stat is unreadable). total = memory.max when
    limited, else psutil.virtual_memory().total (host total). If even
    memory.current is unavailable (not in a cgroup v2 container), falls back
    entirely to psutil: (vm.total - vm.available, vm.total, False).
    """
    current = _read_cgroup_int("memory.current")
    if current is None:
        vm = psutil.virtual_memory()
        return vm.total - vm.available, vm.total, False

    inactive_file = None
    try:
        for line in (_CGROUP_DIR / "memory.stat").read_text().splitlines():
            key, _, value = line.partition(" ")
            if key == "inactive_file":
                inactive_file = int(value)
                break
    except (OSError, ValueError):
        inactive_file = None

    used = current - inactive_file if inactive_file is not None else current

    limit = _read_cgroup_int("memory.max")
    if limit is not None:
        return used, limit, True
    return used, psutil.virtual_memory().total, False


def _cpu_status() -> tuple[float, float]:
    """(load_1min, effective_cpus). effective_cpus from cpu.max quota/period
    when limited, else os.cpu_count()."""
    load_1min = os.getloadavg()[0]

    try:
        quota_str, period_str = (_CGROUP_DIR / "cpu.max").read_text().split()
    except OSError:
        quota_str, period_str = "max", "100000"

    effective_cpus = None
    if quota_str != "max":
        try:
            effective_cpus = int(quota_str) / int(period_str)
        except (ValueError, ZeroDivisionError):
            effective_cpus = None

    if effective_cpus is None:
        effective_cpus = float(os.cpu_count() or 1)

    return load_1min, effective_cpus


def _disk_status() -> tuple[int, int]:
    """(used_bytes, total_bytes) for the filesystem hosting Path.home()."""
    usage = shutil.disk_usage(Path.home())
    return usage.used, usage.total


def _safe_fraction(used, total) -> float:
    """used / total, or raise if total is zero/None (row is then unavailable)."""
    if not total:
        raise ValueError("unavailable")
    return used / total


class SystemResourcesWidget(ControlSectionWidget):
    description = "Memory, CPU and disk usage of this container."
    _THRESHOLDS: ClassVar[tuple] = ((0.75, "success"), (0.90, "warning"))

    _LABEL_WIDTH = "90px"

    def __init__(self):
        self._memory_bar = ipw.FloatProgress(min=0, max=1)
        self._memory_label = ipw.HTML()
        self._cpu_bar = ipw.FloatProgress(min=0, max=1)
        self._cpu_label = ipw.HTML()
        self._disk_bar = ipw.FloatProgress(min=0, max=1)
        self._disk_label = ipw.HTML()

        super().__init__(
            body_children=[
                ipw.HBox(
                    [
                        ipw.HTML(
                            "<b>Memory</b>",
                            layout=ipw.Layout(width=self._LABEL_WIDTH),
                        ),
                        self._memory_bar,
                        self._memory_label,
                    ]
                ),
                ipw.HBox(
                    [
                        ipw.HTML(
                            "<b>CPU load</b>",
                            layout=ipw.Layout(width=self._LABEL_WIDTH),
                        ),
                        self._cpu_bar,
                        self._cpu_label,
                    ]
                ),
                ipw.HBox(
                    [
                        ipw.HTML(
                            "<b>Disk</b>", layout=ipw.Layout(width=self._LABEL_WIDTH)
                        ),
                        self._disk_bar,
                        self._disk_label,
                    ]
                ),
            ]
        )
        self.refresh()

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
        label.value = _state_span("error", str(exc))

    def _do_refresh(self):
        try:
            used, total, limited = _memory_status()
            fraction = _safe_fraction(used, total)
            suffix = "" if limited else " — no container limit, showing host total"
            text = (
                f"{_format_bytes(used)} / {_format_bytes(total)} "
                f"({fraction:.0%}){suffix}"
            )
            self._set_row(self._memory_bar, self._memory_label, fraction, text)
        except Exception as exc:
            self._set_row_error(self._memory_bar, self._memory_label, exc)

        try:
            load_1min, cpus = _cpu_status()
            fraction = _safe_fraction(load_1min, cpus)
            text = f"load {load_1min:.2f} / {cpus:.0f} CPUs ({fraction:.0%})"
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

    For sqlite_dos this directory also holds the database file, since that
    backend keeps both in a single directory.
    """
    backend = profile.storage_backend
    config = profile.storage_config
    if backend == "core.psql_dos":
        return Path(config["repository_uri"].removeprefix("file://"))
    if backend == "core.sqlite_dos":
        return Path(config["filepath"])
    raise ValueError(f"not available for backend {backend}")


def _du_bytes(path) -> int:
    """Size in bytes of `path`, computed with `du -sb`.

    `du` is a fast, C implementation; `os.walk` is much slower on
    object-store directories with many files. `du` can emit warnings about
    transient files mid-scan and still exit nonzero, so the numeric stdout
    is trusted even when the return code is nonzero.
    """
    if not Path(path).exists():
        raise FileNotFoundError(f"{path} not available")
    result = subprocess.run(
        ["du", "-sb", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        return int(result.stdout.split()[0])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(f"could not determine size of {path}") from exc


def _database_size_bytes(storage) -> int:
    """PostgreSQL database size in bytes (psql_dos only)."""
    with storage.get_session() as session:
        return session.execute(
            text("SELECT pg_database_size(current_database())")
        ).scalar()


class _ListLogHandler(logging.Handler):
    """Collects formatted log records so they can be shown to the user."""

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        self.lines.append(self.format(record))


class StorageWidget(ControlSectionWidget):
    """Breaks down disk usage for the loaded profile and runs storage maintenance."""

    description = "Disk usage of profile data and apps; storage maintenance."

    def __init__(self):
        self._maintaining = False

        self._table = ipw.HTML()

        self._dry_run_checkbox = ipw.Checkbox(
            description="Dry run (only report what would be done)", value=True
        )
        self._full_checkbox = ipw.Checkbox(
            description=(
                "Full maintenance (requires exclusive access; stop the daemon first)"
            ),
            value=False,
        )
        self._maintain_button = ipw.Button(
            description="Run maintenance", button_style="warning", icon="wrench"
        )
        self._maintain_button.on_click(self._on_maintain_clicked)
        self._maintain_output = ipw.HTML()

        super().__init__(
            body_children=[
                self._table,
                ipw.HTML("<hr><h4>Maintenance</h4>"),
                self._dry_run_checkbox,
                self._full_checkbox,
                self._maintain_button,
                self._maintain_output,
            ]
        )
        self.refresh()

    @staticmethod
    def _row(label, text, error=False):
        color = _STATE_COLORS["error"] if error else "inherit"
        return (
            "<tr>"
            f"<td style='padding:1px 8px;'><b>{html.escape(label)}</b></td>"
            f"<td style='color:{color};padding:1px 8px;'>{html.escape(text)}</td>"
            "</tr>"
        )

    def _do_refresh(self):
        self._update_table()

    def _update_table(self):
        profile = aiida.get_profile()
        rows = []

        try:
            repo_path = _repository_path(profile)
            size = _du_bytes(repo_path)
            rows.append(
                self._row("File repository", f"{_format_bytes(size)} — {repo_path}")
            )
        except Exception as exc:
            rows.append(self._row("File repository", str(exc), error=True))

        try:
            backend = profile.storage_backend
            if backend == "core.psql_dos":
                storage = manage.get_manager().get_profile_storage()
                db_bytes = _database_size_bytes(storage)
                rows.append(self._row("Database", _format_bytes(db_bytes)))
            elif backend == "core.sqlite_dos":
                rows.append(
                    self._row("Database", "included in the repository directory")
                )
            else:
                rows.append(
                    self._row(
                        "Database", f"not available for backend {backend}", error=True
                    )
                )
        except Exception as exc:
            rows.append(self._row("Database", str(exc), error=True))

        try:
            apps_dir = os.environ.get("AIIDALAB_APPS", "/project/apps")
            size = _du_bytes(apps_dir)
            rows.append(
                self._row("Installed apps", f"{_format_bytes(size)} — {apps_dir}")
            )
        except Exception as exc:
            rows.append(self._row("Installed apps", str(exc), error=True))

        try:
            used, total = _disk_status()
            rows.append(
                self._row(
                    "Home filesystem",
                    f"{_format_bytes(used)} used of {_format_bytes(total)}",
                )
            )
        except Exception as exc:
            rows.append(self._row("Home filesystem", str(exc), error=True))

        self._table.value = (
            f'<h4>Storage — profile "{html.escape(profile.name)}"</h4>'
            "<table style='border-collapse:collapse;'>" + "".join(rows) + "</table>"
        )

    def _on_maintain_clicked(self, _=None):
        if self._maintaining:
            return
        self._maintaining = True
        self._maintain_button.disabled = True
        self._dry_run_checkbox.disabled = True
        self._full_checkbox.disabled = True
        self._maintain_output.value = (
            "Running maintenance... <i class='fa fa-spinner fa-spin'></i>"
        )

        full = self._full_checkbox.value
        dry_run = self._dry_run_checkbox.value

        def worker():
            try:
                daemon = engine.daemon.get_daemon_client()
                if full and daemon.is_daemon_running:
                    self._maintain_output.value = _state_span(
                        "warning",
                        "Full maintenance requires exclusive access to the "
                        "profile. Stop the daemon first (see the Daemon "
                        "section) and try again.",
                    )
                    return

                profile = aiida.get_profile()
                storage = manage.get_manager().get_profile_storage()

                before = None
                if not dry_run:
                    try:
                        before = _du_bytes(_repository_path(profile))
                    except Exception:
                        before = None

                handler = _ListLogHandler()
                handler.setLevel(logging.INFO)
                logger = logging.getLogger("aiida.storage")
                previous_level = logger.level
                logger.setLevel(logging.INFO)
                logger.addHandler(handler)
                try:
                    storage.maintain(full=full, dry_run=dry_run)
                finally:
                    logger.removeHandler(handler)
                    logger.setLevel(previous_level)

                log_html = (
                    "<br>".join(html.escape(line) for line in handler.lines)
                    if handler.lines
                    else "(no log output)"
                )

                if not dry_run and before is not None:
                    try:
                        after = _du_bytes(_repository_path(profile))
                        diff = before - after
                        if diff > 0:
                            log_html += f"<br><b>Reclaimed {_format_bytes(diff)}.</b>"
                        else:
                            log_html += "<br><b>No change in repository size.</b>"
                    except Exception:
                        pass

                self._maintain_output.value = (
                    _state_span("ok", "Maintenance finished.") + f"<br>{log_html}"
                )
            except Exception as exc:
                self._maintain_output.value = _state_span(
                    "error", f"Maintenance failed: {exc}"
                )
            finally:
                # Re-enable the controls before refreshing the table: if the
                # refresh raises, the page must not be left with the
                # controls permanently disabled.
                self._maintain_button.disabled = False
                self._dry_run_checkbox.disabled = False
                self._full_checkbox.disabled = False
                self._maintaining = False
                try:
                    self.refresh()
                except Exception as exc:
                    self._maintain_output.value += "<br>" + _state_span(
                        "error", f"Failed to refresh the storage breakdown: {exc}"
                    )

        threading.Thread(target=worker, daemon=True).start()


class ProcessControlWidget(ControlSectionWidget):
    description = "Inspect, pause, resume or kill AiiDA processes."

    def __init__(self):
        process_list = process.ProcessListWidget(path_to_root="../")
        self.process_list = process_list
        past_days_widget = ipw.IntText(value=7, description="Past days:")
        tr.link((past_days_widget, "value"), (process_list, "past_days"))

        all_days_checkbox = ipw.Checkbox(description="All days", value=True)
        tr.dlink((all_days_checkbox, "value"), (past_days_widget, "disabled"))
        tr.dlink(
            (all_days_checkbox, "value"),
            (process_list, "past_days"),
            transform=lambda v: -1 if v else past_days_widget.value,
        )

        available_states = [state.value for state in plumpy.ProcessState]
        process_state_widget = ipw.SelectMultiple(
            options=available_states,
            value=["running", "waiting"],
            description="Process State:",
            style={"description_width": "initial"},
            disabled=False,
        )
        tr.dlink((process_state_widget, "value"), (process_list, "process_states"))

        self.process_select = ipw.SelectMultiple(
            description="Act on:",
            options=[],
            rows=8,
            layout=ipw.Layout(width="600px"),
            style={"description_width": "initial"},
        )
        self.process_select.observe(self._on_selection_change, names="value")
        process_list.observe(self._on_process_list_updated, names="updated")

        self.pause_button = ipw.Button(description="Pause", disabled=True, icon="pause")
        self.pause_button.on_click(self._on_pause_clicked)
        self.play_button = ipw.Button(description="Play", disabled=True, icon="play")
        self.play_button.on_click(self._on_play_clicked)
        self.kill_button = ipw.Button(
            description="Kill", button_style="danger", disabled=True, icon="times"
        )
        self.kill_button.on_click(self._on_kill_clicked)
        self._action_status = ipw.HTML()

        self._action_running = False
        self._kill_armed = False

        super().__init__(
            body_children=[
                ipw.HBox(
                    [
                        process_state_widget,
                        ipw.VBox([past_days_widget, all_days_checkbox]),
                    ]
                ),
                process_list,
                ipw.HTML("<h4>Actions</h4>"),
                self.process_select,
                ipw.HBox([self.pause_button, self.play_button, self.kill_button]),
                self._action_status,
            ]
        )
        self.refresh()

    def _do_refresh(self):
        self.process_list.update()

    def _on_process_list_updated(self, _=None):
        self._rebuild_options()
        self._disarm_kill()
        self._sync_action_buttons_disabled()

    def _rebuild_options(self):
        rows = self.process_list.current_rows.get("rows", [])
        previous_selection = set(self.process_select.value)

        options = []
        for row in rows:
            try:
                pk = int(row[process.HEADER_PK])
            except (KeyError, ValueError, TypeError):
                continue
            label = (
                f"{pk} | {row.get(process.HEADER_PROCESS_LABEL, '')} | "
                f"{row.get(process.HEADER_STATE, '')}"
            )
            options.append((label, pk))

        self.process_select.options = options
        self.process_select.value = tuple(
            pk for _, pk in options if pk in previous_selection
        )

    def _on_selection_change(self, _=None):
        self._disarm_kill()
        self._sync_action_buttons_disabled()

    def _sync_action_buttons_disabled(self):
        disabled = self._action_running or not self.process_select.value
        self.pause_button.disabled = disabled
        self.play_button.disabled = disabled
        self.kill_button.disabled = disabled

    def _disarm_kill(self):
        self._kill_armed = False
        self.kill_button.description = "Kill"

    def _on_pause_clicked(self, _=None):
        self._disarm_kill()
        self._dispatch_action(
            action_name="pause the process(es)",
            control_func=process_control.pause_processes,
            verb="Pause",
        )

    def _on_play_clicked(self, _=None):
        self._disarm_kill()
        self._dispatch_action(
            action_name="play the process(es)",
            control_func=process_control.play_processes,
            verb="Play",
        )

    def _on_kill_clicked(self, _=None):
        selected = self.process_select.value
        if not selected:
            self._action_status.value = "Select at least one process."
            return
        if not self._kill_armed:
            self._kill_armed = True
            self.kill_button.description = f"Confirm kill ({len(selected)})"
            return
        self._dispatch_action(
            action_name="kill the process(es)",
            control_func=process_control.kill_processes,
            verb="Kill",
        )
        self._disarm_kill()

    def _dispatch_action(self, action_name, control_func, verb):
        selected_pks = list(self.process_select.value)
        if not selected_pks:
            self._action_status.value = "Select at least one process."
            return

        if not engine.daemon.get_daemon_client().is_daemon_running:
            self._action_status.value = _state_span(
                "warning",
                "Process actions need the running daemon (see the Daemon "
                "section above).",
            )
            return

        self._action_running = True
        self._sync_action_buttons_disabled()
        self._action_status.value = (
            f"{verb} request in progress... <i class='fa fa-spinner fa-spin'></i>"
        )

        def worker():
            try:
                nodes = []
                errors = []
                for pk in selected_pks:
                    try:
                        nodes.append(orm.load_node(pk))
                    except NotExistent as exc:
                        errors.append(f"PK {pk}: {exc}")

                if nodes:
                    control_func(nodes, timeout=5.0)

                messages = []
                if nodes:
                    messages.append(
                        _state_span(
                            "ok",
                            f"{verb} request sent to {len(nodes)} process(es). "
                            "States may take a few seconds to change.",
                        )
                    )
                if errors:
                    messages.append(
                        f"<span style='color:{_STATE_COLORS['error']}'>"
                        + "<br>".join(html.escape(e) for e in errors)
                        + "</span>"
                    )
                self._action_status.value = "<br>".join(messages) or "Nothing to do."
            except process_control.ProcessTimeoutException as exc:
                self._action_status.value = _state_span(
                    "error", f"Timed out trying to {action_name}: {exc}"
                )
            except Exception as exc:
                self._action_status.value = _state_span(
                    "error", f"Failed to {action_name}: {exc}"
                )
            finally:
                # Re-enable the buttons before refreshing the process list:
                # if the refresh raises, the page must not be left with the
                # buttons permanently disabled.
                self._action_running = False
                self._sync_action_buttons_disabled()
                try:
                    self.process_list.update()
                except Exception as exc:
                    self._action_status.value += "<br>" + _state_span(
                        "error", f"Failed to refresh the process list: {exc}"
                    )

        threading.Thread(target=worker, daemon=True).start()


class Profile(ipw.HBox):
    def __init__(self, profile, is_default, is_loaded, on_make_default, on_delete):
        self.profile = profile

        name_html = html.escape(profile.name)
        if is_default:
            name_html += (
                " <span style='background:"
                f"{Theme.COLORS.CHECK};color:white;padding:1px 8px;"
                "border-radius:9px;font-size:11px;'>default</span>"
            )
        if is_loaded:
            name_html += f" <span style='color:{Theme.COLORS.GRAY}'>(in use)</span>"
        self.name = ipw.HTML(name_html, layout=ipw.Layout(width="220px"))

        self.make_default = ipw.Button(
            description="Make default", button_style="info", icon="star"
        )
        if is_default:
            self.make_default.disabled = True
            self.make_default.tooltip = "Already the default profile"
        else:
            self.make_default.on_click(lambda _: on_make_default(profile.name))

        self.delete = ipw.Button(
            description="Delete", button_style="danger", icon="trash"
        )
        if is_loaded:
            self.delete.disabled = True
            self.delete.tooltip = "This profile is currently in use by this notebook and cannot be deleted"
        else:
            self.delete.on_click(lambda _: on_delete(profile.name))

        super().__init__(children=[self.name, self.make_default, self.delete])


class ProfileControlWidget(ControlSectionWidget):
    description = "Manage AiiDA profiles: default profile and deletion."

    def __init__(self):
        self._confirm_target = None
        self._confirm_warning = ipw.HTML(value="")
        self._confirm_delete_storage = ipw.Checkbox(
            description="Also delete all data of this profile (database and file repository)",
            value=False,
        )
        self._confirm_button = ipw.Button(
            description="Confirm delete", button_style="danger", icon="trash"
        )
        self._confirm_button.on_click(self._on_confirm_delete)
        self._cancel_button = ipw.Button(description="Cancel", icon="times")
        self._cancel_button.on_click(self._on_cancel_delete)
        self._confirm_box = ipw.VBox(
            children=[
                self._confirm_warning,
                self._confirm_delete_storage,
                ipw.HBox([self._confirm_button, self._cancel_button]),
            ]
        )
        self._confirm_box.layout.display = "none"

        self._rows_box = ipw.VBox()

        super().__init__(
            body_children=[
                self._rows_box,
                self._confirm_box,
            ]
        )
        self.refresh()

    def _do_refresh(self):
        config = manage.get_config()
        loaded_profile = get_profile()
        loaded_profile_name = (
            loaded_profile.name if loaded_profile is not None else None
        )
        rows = [
            Profile(
                p,
                is_default=(p.name == config.default_profile_name),
                is_loaded=(p.name == loaded_profile_name),
                on_make_default=self._on_make_default,
                on_delete=self._on_delete_clicked,
            )
            for p in config.profiles
        ]
        self._rows_box.children = rows

    def _on_make_default(self, name):
        try:
            config = manage.get_config()
            config.set_default_profile(name, overwrite=True)
            config.store()
        except Exception as exc:
            self.show_error(f"Error: {exc}")
            return
        self.show_success(f'Profile "{name}" is now the default.')
        self._do_refresh()

    def _on_delete_clicked(self, name):
        self._confirm_target = name
        self._confirm_delete_storage.value = False
        self._confirm_warning.value = (
            f"You are about to delete profile <b>{name}</b>. This cannot be undone."
        )
        self._confirm_box.layout.display = ""

    def _on_cancel_delete(self, _=None):
        self._confirm_target = None
        self._confirm_box.layout.display = "none"

    def _on_confirm_delete(self, _=None):
        name = self._confirm_target
        if name is None:
            return
        delete_storage = self._confirm_delete_storage.value
        try:
            config = manage.get_config()
            config.delete_profile(name, delete_storage=delete_storage)
        except Exception as exc:
            self.show_error(f"Error: {exc}")
        else:
            self.show_success(f'Profile "{name}" deleted.')
        self._confirm_target = None
        self._confirm_box.layout.display = "none"
        self._do_refresh()


_RESET_MODE_LABELS = {
    "1": "Reset installed apps and local software",
    "2": "Full factory reset — erase everything",
}

_RESET_MODE_DETAILS = {
    "1": "Removes ~/apps and ~/.local. AiiDA data and profiles survive.",
    "2": (
        "Erases EVERYTHING in the home directory, including all AiiDA "
        "data, profiles, and files."
    ),
}

_RESET_MODE_DESCRIPTIONS = {
    mode: f"{_RESET_MODE_LABELS[mode]} — {_RESET_MODE_DETAILS[mode]}"
    for mode in _RESET_MODE_LABELS
}

_FACTORY_RESET_CONFIRM_PHRASE = "factory-reset"


class DangerZoneWidget(ControlSectionWidget):
    """Schedules a factory reset performed by the container on next restart."""

    def __init__(self):
        self._env_warning = ipw.HTML()

        self._mode_radio = ipw.RadioButtons(
            options=[(label, mode) for mode, label in _RESET_MODE_LABELS.items()],
            value="1",
        )
        self._mode_details = ipw.HTML()
        self._mode_radio.observe(self._on_mode_change, names="value")
        self._confirm_text = ipw.Text(
            placeholder=(
                f"Type `{_FACTORY_RESET_CONFIRM_PHRASE}` to enable the button"
            ),
        )
        self._confirm_text.observe(self._on_confirm_text_change, names="value")
        self._schedule_button = ipw.Button(
            description="Schedule factory reset",
            button_style="danger",
            disabled=True,
            icon="exclamation-triangle",
        )
        self._schedule_button.on_click(self._on_schedule_clicked)
        self._schedule_status = ipw.HTML()
        self._schedule_box = ipw.VBox(
            children=[
                ipw.HTML(
                    "A factory reset is performed by the AiiDAlab container on "
                    "the <b>next restart</b>. Choose what to reset:"
                ),
                self._mode_radio,
                self._mode_details,
                self._confirm_text,
                self._schedule_button,
                self._schedule_status,
            ]
        )
        self._on_mode_change({"new": self._mode_radio.value})

        self._scheduled_alert = ipw.HTML()
        self._cancel_button = ipw.Button(
            description="Cancel scheduled reset", icon="undo"
        )
        self._cancel_button.on_click(self._on_cancel_clicked)
        self._cancel_status = ipw.HTML()
        self._scheduled_box = ipw.VBox(
            children=[
                self._scheduled_alert,
                self._cancel_button,
                self._cancel_status,
            ]
        )

        super().__init__(
            body_children=[
                self._env_warning,
                self._schedule_box,
                self._scheduled_box,
            ],
        )
        self.layout.border = f"1px solid {Theme.COLORS.DANGER}"
        self.layout.padding = "12px"
        self.refresh()

    def _on_mode_change(self, change):
        self._mode_details.value = html.escape(_RESET_MODE_DETAILS[change["new"]])

    def _on_confirm_text_change(self, change):
        self._schedule_button.disabled = (
            change["new"].strip() != _FACTORY_RESET_CONFIRM_PHRASE
        )

    def _on_schedule_clicked(self, _=None):
        mode = self._mode_radio.value
        try:
            _FACTORY_RESET_FILE.write_text(mode)
            written_back = _FACTORY_RESET_FILE.read_text()
        except Exception as exc:
            self._schedule_status.value = _state_span(
                "error", f"Failed to schedule factory reset: {exc}"
            )
            return
        if written_back != mode:
            self._schedule_status.value = _state_span(
                "error",
                "Failed to schedule factory reset: could not verify the flag "
                "file content",
            )
            return
        self._confirm_text.value = ""
        self._schedule_status.value = ""
        self._do_refresh()

    def _on_cancel_clicked(self, _=None):
        try:
            _FACTORY_RESET_FILE.unlink(missing_ok=True)
        except Exception as exc:
            self._cancel_status.value = _state_span(
                "error", f"Failed to cancel the scheduled reset: {exc}"
            )
            return
        self._cancel_status.value = ""
        self._do_refresh()

    def _do_refresh(self):
        env_value = os.environ.get("AIIDALAB_FACTORY_RESET")
        if env_value and env_value != "0":
            self._env_warning.value = (
                f"<div style='color:{_STATE_COLORS['error']}'><b>Warning:</b> the "
                "<code>AIIDALAB_FACTORY_RESET</code> environment variable is "
                f"set (<code>{html.escape(env_value)}</code>) and takes "
                "precedence over anything configured here.</div>"
            )
        else:
            self._env_warning.value = ""

        if _FACTORY_RESET_FILE.exists():
            try:
                content = _FACTORY_RESET_FILE.read_text().strip()
            except Exception as exc:
                description = f"could not be read: {html.escape(str(exc))}"
            else:
                description = html.escape(
                    _RESET_MODE_DESCRIPTIONS.get(content, f"unknown mode '{content}'")
                )
            self._scheduled_alert.value = (
                "<div style='background:#fff3cd;color:#7a5b00;padding:8px;"
                "border-radius:4px;'>"
                f"A factory reset ({description}) is scheduled. It will be "
                "performed the next time this container is restarted (e.g. "
                "<code>aiidalab-launch restart</code>, or restart the "
                "container from Docker). Until then it can be "
                "cancelled.</div>"
            )
            self._schedule_box.layout.display = "none"
            self._scheduled_box.layout.display = ""
        else:
            self._schedule_box.layout.display = ""
            self._scheduled_box.layout.display = "none"
