import html
import threading
from typing import ClassVar

import aiida
import ipywidgets as ipw
import plumpy
import traitlets as tr
from aiida import engine, get_profile, manage, orm
from aiida.engine.daemon.client import DaemonException

from home import process


class DaemonControlWidget(ipw.VBox):
    def __init__(self):
        self._daemon = engine.daemon.get_daemon_client()
        self._status = ipw.HTML()

        # Start daemon.
        self.start_button = ipw.Button(description="Start daemon", button_style="info")
        self.start_button.on_click(self._start_daemon)

        # Stop daemon.
        self.stop_button = ipw.Button(description="Stop daemon", button_style="danger")
        self.stop_button.on_click(self._stop_daemon)

        # Restart daemon.
        self.restart_button = ipw.Button(
            description="Restart daemon", button_style="warning"
        )
        self.restart_button.on_click(self._restart_daemon)

        # Refresh status.
        self.refresh_button = ipw.Button(description="Refresh")
        self.refresh_button.on_click(self._refresh_status)

        self._action_buttons = [
            self.start_button,
            self.stop_button,
            self.restart_button,
            self.refresh_button,
        ]

        self.info = ipw.HTML()
        self._update_status()
        super().__init__(
            children=[
                self.info,
                self._status,
                ipw.HBox(self._action_buttons),
            ]
        )

    def _start_daemon(self, _=None):
        if self._daemon.is_daemon_running:
            self.info.value = "The daemon is already running."
            return
        self._run_action(
            action_name="start the daemon",
            action=self._daemon.start_daemon,
            in_progress_message="Starting the daemon...",
            success_message="The daemon has been started.",
        )

    def _stop_daemon(self, _=None):
        if not self._daemon.is_daemon_running:
            self.info.value = "The daemon is not running."
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

    def _refresh_status(self, _=None):
        # The actual refresh happens in _run_action's finally block, which
        # already updates the status after every action.
        self._run_action(
            action_name="refresh the status",
            action=lambda: None,
            in_progress_message="Refreshing the status...",
            success_message="Status refreshed.",
        )

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
                self.info.value = (
                    f"<span style='color:red'>Failed to {action_name}: {exc}</span>"
                )
            else:
                self.info.value = f"<span style='color:green'>{success_message}</span>"
            finally:
                self._update_status()
                for button in self._action_buttons:
                    button.disabled = False

        threading.Thread(target=worker, daemon=True).start()

    _ROW_STYLES: ClassVar[dict] = {
        "ok": ("green", "&#10003;"),
        "error": ("red", "&#10007;"),
        "warning": ("#b58900", "&#9888;"),
    }

    @classmethod
    def _status_row(cls, state, label, text):
        color, symbol = cls._ROW_STYLES[state]
        text_color = "inherit" if state == "ok" else color
        return (
            f"<tr>"
            f"<td style='color:{color};padding:1px 8px;'>{symbol}</td>"
            f"<td style='padding:1px 8px;'><b>{label}</b></td>"
            f"<td style='color:{text_color};padding:1px 8px;'>{html.escape(text)}</td>"
            f"</tr>"
        )

    def _update_status(self, _=None):
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
            rows.append(self._status_row("ok", "storage", str(storage)))
        except Exception as exc:
            rows.append(self._status_row("error", "storage", str(exc)))

        try:
            broker = manage.get_manager().get_broker()
            if broker is None:
                rows.append(
                    self._status_row("warning", "broker", "No broker configured")
                )
            else:
                rows.append(self._status_row("ok", "broker", str(broker)))
        except Exception as exc:
            rows.append(self._status_row("error", "broker", str(exc)))

        try:
            # get_worker_info() blocks for the client timeout when the daemon
            # is down, so only call it after confirming the daemon is running.
            daemon_row = self._status_row("warning", "daemon", "Daemon is not running")
            if self._daemon.is_daemon_running:
                try:
                    workers = len(self._daemon.get_worker_info().get("info", []))
                    daemon_row = self._status_row(
                        "ok", "daemon", f"Daemon is running with {workers} worker(s)"
                    )
                except DaemonException:
                    pass  # The daemon stopped between the check and the call.
            rows.append(daemon_row)
        except Exception as exc:
            rows.append(self._status_row("error", "daemon", str(exc)))

        self._status.value = (
            "<table style='border-collapse:collapse;'>" + "".join(rows) + "</table>"
        )


class ProcessControlWidget(ipw.VBox):
    def __init__(self):
        process_list = process.ProcessListWidget(path_to_root="../")
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
        process_list.update()

        super().__init__(
            children=[
                ipw.HBox([past_days_widget, all_days_checkbox]),
                process_state_widget,
                process_list,
            ]
        )


class GroupControlWidget(ipw.VBox):
    def __init__(self):
        text = ipw.HTML("I am a Group Control Page")
        super().__init__(children=[text])


class Profile(ipw.HBox):
    def __init__(self, profile, is_default, is_loaded, on_make_default, on_delete):
        self.profile = profile

        label = f"{profile.name} (default)" if is_default else profile.name
        self.name = ipw.HTML(f"""<font size="3"> * {label}</font>""")

        self.make_default = ipw.Button(description="Make default", button_style="info")
        if is_default:
            self.make_default.disabled = True
            self.make_default.tooltip = "Already the default profile"
        else:
            self.make_default.on_click(lambda _: on_make_default(profile.name))

        self.delete = ipw.Button(description="Delete", button_style="danger")
        if is_loaded:
            self.delete.disabled = True
            self.delete.tooltip = "This profile is currently in use by this notebook and cannot be deleted"
        else:
            self.delete.on_click(lambda _: on_delete(profile.name))

        super().__init__(children=[self.name, self.make_default, self.delete])


class ProfileControlWidget(ipw.VBox):
    def __init__(self):
        self._title = ipw.HTML(value="<h3> List of profiles </h3>")
        self._status = ipw.HTML(value="")

        self._confirm_target = None
        self._confirm_warning = ipw.HTML(value="")
        self._confirm_delete_storage = ipw.Checkbox(
            description="Also delete all data of this profile (database and file repository)",
            value=False,
        )
        self._confirm_button = ipw.Button(
            description="Confirm delete", button_style="danger"
        )
        self._confirm_button.on_click(self._on_confirm_delete)
        self._cancel_button = ipw.Button(description="Cancel")
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
        self._refresh()

        super().__init__(
            children=[
                self._title,
                self._rows_box,
                self._confirm_box,
                self._status,
            ]
        )

    def _refresh(self, _=None):
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
            self._status.value = f'<font color="red">Error: {exc}</font>'
            return
        self._status.value = (
            f'<font color="green">Profile "{name}" is now the default.</font>'
        )
        self._refresh()

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
            self._status.value = f'<font color="red">Error: {exc}</font>'
        else:
            self._status.value = f'<font color="green">Profile "{name}" deleted.</font>'
        self._confirm_target = None
        self._confirm_box.layout.display = "none"
        self._refresh()
