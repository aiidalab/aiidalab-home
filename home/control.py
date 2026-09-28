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

import aiida
import ipywidgets as ipw
import sqlalchemy as sa
from aiida import get_profile, manage, orm
from aiida.engine.daemon.client import DaemonException
from aiida.storage.log import STORAGE_LOGGER
from aiidalab.config import AIIDALAB_APPS

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


class DaemonControlWidget(ControlSectionWidget):
    description = "The daemon runs your AiiDA processes in the background."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])


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
        return Path(profile.storage_config["repository_uri"].removeprefix("file://"))
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
            description="Full maintenance (requires the daemon to be stopped)",
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
                "Stop the daemon first (see the Daemon tab) and try again.",
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


class ProcessControlWidget(ControlSectionWidget):
    description = "Inspect, pause, resume or kill AiiDA processes."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])


class ProfileControlWidget(ControlSectionWidget):
    description = "Manage AiiDA profiles: default profile and deletion."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])


class DangerZoneWidget(ControlSectionWidget):
    description = "Irreversible actions that can lead to data loss."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])
