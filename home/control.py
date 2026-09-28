from __future__ import annotations

import html
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import ClassVar, Protocol

import aiida
import ipywidgets as ipw
from aiida import get_profile, manage, orm
from aiida.engine.daemon.client import DaemonException

from home.themes import ThemeDefault as Theme

logger = logging.getLogger(__name__)


class _DaemonClient(Protocol):
    """The subset of `DaemonClient` that `_probe_daemon` relies on."""

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
                        f"Last updated: {datetime.now().strftime('%H:%M:%S')}"
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


class StorageWidget(ControlSectionWidget):
    description = "Disk usage of profile data and apps; storage maintenance."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])


class ProcessControlWidget(ControlSectionWidget):
    description = "Inspect, pause, resume or kill AiiDA processes."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])


class ProfileControlWidget(ControlSectionWidget):
    description = "Manage AiiDA profiles: default profile and deletion."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])


# The container's entrypoint (aiidalab-docker-stack,
# before-notebook.d/50_factory-reset.sh) reads this file on start, performs
# the reset mode it contains and removes it. A module constant so tests can
# monkeypatch it: the real file erases data on the next restart.
_FACTORY_RESET_FILE = Path.home() / "AIIDALAB_FACTORY_RESET"

# The reset modes of that script: (label, details).
_RESET_MODES = {
    "1": (
        "Reset installed apps and local software",
        "Removes ~/apps and ~/.local. AiiDA data and profiles survive.",
    ),
    "2": (
        "Full factory reset — erase everything",
        (
            "Erases EVERYTHING in the home directory, including all AiiDA "
            "data, profiles and files."
        ),
    ),
}

_FACTORY_RESET_CONFIRM_PHRASE = "factory-reset"


class DangerZoneWidget(ControlSectionWidget):
    description = "Irreversible actions that can lead to data loss."

    def __init__(self):
        self._env_warning = ipw.HTML()

        self._mode = ipw.RadioButtons(
            options=[(label, mode) for mode, (label, _) in _RESET_MODES.items()],
            value="1",
            layout=ipw.Layout(width="auto"),
        )
        self._mode_details = ipw.HTML()
        self._mode.observe(self._show_mode_details, names="value")
        self._show_mode_details()
        self._confirm_text = ipw.Text()
        self._confirm_text.observe(self._on_confirm_text_change, names="value")
        self._schedule_button = ipw.Button(
            description="Schedule factory reset",
            button_style="danger",
            icon="exclamation-triangle",
            disabled=True,
        )
        self._schedule_button.on_click(self._on_schedule)
        self._schedule_box = ipw.VBox(
            [
                ipw.HTML(
                    "A factory reset is performed by the AiiDAlab container on "
                    "its <b>next restart</b>. Choose what to reset:"
                ),
                self._mode,
                self._mode_details,
                ipw.HTML(
                    f"To confirm, type <code>{_FACTORY_RESET_CONFIRM_PHRASE}</code>:"
                ),
                self._confirm_text,
                self._schedule_button,
            ]
        )

        self._scheduled_alert = ipw.HTML()
        cancel_button = ipw.Button(description="Cancel scheduled reset", icon="undo")
        cancel_button.on_click(self._on_cancel)
        self._scheduled_box = ipw.VBox(
            [self._scheduled_alert, cancel_button],
            layout=ipw.Layout(display="none"),
        )

        super().__init__([self._env_warning, self._schedule_box, self._scheduled_box])
        self.layout.border = f"1px solid {Theme.COLORS.DANGER}"
        self.layout.padding = "12px"

    def _show_mode_details(self, _=None):
        _, details = _RESET_MODES[self._mode.value]
        self._mode_details.value = html.escape(details)

    def _on_confirm_text_change(self, change):
        self._schedule_button.disabled = (
            change["new"].strip() != _FACTORY_RESET_CONFIRM_PHRASE
        )

    def _on_schedule(self, _=None):
        mode = self._mode.value
        try:
            _FACTORY_RESET_FILE.write_text(mode)
            # Read it back: a reset the container won't see is worse than an
            # error, e.g. on a home directory mounted with surprising semantics.
            if _FACTORY_RESET_FILE.read_text() != mode:
                raise RuntimeError("could not verify the content of the flag file")
        except Exception as exc:
            self.show_error(f"Failed to schedule the factory reset: {exc}")
        else:
            self.info.value = ""
            self._confirm_text.value = ""
        # Show whatever is on disk now, including a half-written flag file.
        self._do_refresh()

    def _on_cancel(self, _=None):
        try:
            _FACTORY_RESET_FILE.unlink(missing_ok=True)
        except Exception as exc:
            self.show_error(f"Failed to cancel the scheduled reset: {exc}")
        else:
            self.info.value = ""
        self._do_refresh()

    def _do_refresh(self):
        # The container script lets a non-empty variable override the flag
        # file, even "0" (which then cancels the reset scheduled here).
        env_mode = os.environ.get("AIIDALAB_FACTORY_RESET", "")
        self._env_warning.value = (
            _state_span(
                State.WARNING,
                "The AIIDALAB_FACTORY_RESET environment variable of this "
                f"container is set to '{env_mode}', which takes precedence "
                "over a reset scheduled here.",
            )
            if env_mode
            else ""
        )

        scheduled = _FACTORY_RESET_FILE.exists()
        if scheduled:
            self._scheduled_alert.value = self._scheduled_message()
        self._schedule_box.layout.display = "none" if scheduled else ""
        self._scheduled_box.layout.display = "" if scheduled else "none"

    @staticmethod
    def _scheduled_message():
        try:
            mode = _FACTORY_RESET_FILE.read_text().strip()
        except OSError as exc:
            what = f"the flag file could not be read: {exc}"
        else:
            label, details = _RESET_MODES.get(mode, (f"unknown mode '{mode}'", ""))
            what = f"{label}. {details}" if details else label
        return (
            "<div class='alert alert-warning' role='alert'>"
            f"A factory reset is scheduled: <b>{html.escape(what)}</b><br>"
            "It is performed the next time this AiiDAlab container starts, i.e. "
            "after your AiiDAlab server has been stopped and started again. "
            "Until then, it can be cancelled.</div>"
        )
