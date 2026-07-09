import subprocess
import threading

import ipywidgets as ipw
import plumpy
import traitlets as tr
from aiida import engine, get_profile, manage
from aiida.engine.daemon.client import DaemonException
from IPython.display import clear_output

from home import process


class DaemonControlWidget(ipw.VBox):
    def __init__(self):
        self._daemon = engine.daemon.get_daemon_client()
        self._status = ipw.Output()

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

        self.info = ipw.HTML()
        self._update_status()
        super().__init__(
            children=[
                self.info,
                self._status,
                ipw.HBox([self.start_button, self.stop_button, self.restart_button]),
            ]
        )

    def _start_daemon(self, _=None):
        if self._daemon.is_daemon_running:
            self.info.value = "The daemon is already running."
            return
        self._run_action(
            action_name="start",
            action=self._daemon.start_daemon,
            in_progress_message="Starting the daemon...",
            success_message="The daemon has been started.",
        )

    def _stop_daemon(self, _=None):
        if not self._daemon.is_daemon_running:
            self.info.value = "The daemon is not running."
            return
        self._run_action(
            action_name="stop",
            action=self._daemon.stop_daemon,
            in_progress_message="Stopping the daemon...",
            success_message="The daemon has been stopped.",
        )

    def _restart_daemon(self, _=None):
        self._run_action(
            action_name="restart",
            action=self._restart_with_fallback,
            in_progress_message="Restarting the daemon...",
            success_message="The daemon has been restarted.",
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
        self.start_button.disabled = True
        self.stop_button.disabled = True
        self.restart_button.disabled = True

        def worker():
            try:
                action()
            except DaemonException as exc:
                self.info.value = (
                    "<span style='color:red'>"
                    f"Failed to {action_name} the daemon: {exc}</span>"
                )
            except Exception as exc:
                self.info.value = (
                    "<span style='color:red'>"
                    f"Failed to {action_name} the daemon: {exc}</span>"
                )
            else:
                self.info.value = f"<span style='color:green'>{success_message}</span>"
            finally:
                self._update_status()
                self.start_button.disabled = False
                self.stop_button.disabled = False
                self.restart_button.disabled = False

        threading.Thread(target=worker, daemon=True).start()

    def _update_status(self, _=None):
        self._clear_status()
        with self._status:
            result_status = subprocess.run(
                ["verdi", "status"], capture_output=True, text=True, check=False
            )
            print(result_status.stdout, result_status.stderr)

            result_daemon = subprocess.run(
                ["verdi", "daemon", "status"],
                capture_output=True,
                text=True,
                check=False,
            )
            print(result_daemon.stdout, result_daemon.stderr)

    def _clear_status(self):
        with self._status:
            clear_output()


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
