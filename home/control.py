import subprocess

import aiidalab_widgets_base as awb
import ipywidgets as ipw
import plumpy
import traitlets as tr
from aiida import engine, manage
from IPython.display import clear_output


class DaemonControlWidget(ipw.VBox):
    def __init__(self):
        self._daemon = engine.daemon.get_daemon_client()
        self._status = ipw.Output()

        # Start daemon.
        start_button = ipw.Button(description="Start daemon", button_style="info")
        start_button.on_click(self._start_daemon)

        # Stop daemon.
        stop_button = ipw.Button(description="Stop daemon", button_style="danger")
        stop_button.on_click(self._stop_daemon)

        # Restart daemon.
        restart_button = ipw.Button(
            description="Restart daemon", button_style="warning"
        )
        restart_button.on_click(self._restart_daemon)

        self.info = ipw.HTML()
        self._update_status()
        super().__init__(
            children=[
                self.info,
                self._status,
                ipw.HBox([start_button, stop_button, restart_button]),
            ]
        )

    def _restart_daemon(self, _=None):
        self._clear_status()
        self.info.value = "Restarting the daemon..."
        response = self._daemon.restart_daemon()
        self.info.value = ""
        self._update_status()
        return response

    def _start_daemon(self, _=None):
        self._clear_status()
        self.info.value = "Starting the daemon..."
        response = self._daemon.start_daemon()
        self.info.value = ""
        self._update_status()
        return response

    def _stop_daemon(self, _=None):
        self._clear_status()
        self.info.value = "Stopping the daemon..."
        response = self._daemon.stop_daemon()
        self.info.value = ""
        self._update_status()
        return response

    def _update_status(self, _=None):
        self._clear_status()
        with self._status:
            result = subprocess.run(
                ["verdi", "daemon", "status"],
                capture_output=True,
                text=True,
                check=False,
            )
            print(result.stdout, result.stderr)

    def _clear_status(self):
        with self._status:
            clear_output()


class ProcessControlWidget(ipw.VBox):
    def __init__(self):
        process_list = awb.ProcessListWidget(path_to_root="../../")
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


class StatusControlWidget(ipw.HTML):
    def __init__(self):
        print("AiiDA status")
        print(
            subprocess.run(
                ["verdi", "status"], capture_output=True, text=True, check=False
            ).stdout
        )
        super().__init__()


class Profile(ipw.HBox):
    def __init__(self, profile):
        self.profile = profile
        self.name = ipw.HTML(f"""<font size="3"> * {self.profile.name}</font>""")
        self.make_default = ipw.Button(description="Make default", button_style="info")
        self.delete = ipw.Button(description="Delele", button_style="danger")
        super().__init__(children=[self.name, self.make_default, self.delete])


class ProfileControlWidget(ipw.VBox):
    def __init__(self):
        text = ipw.HTML(value="<h3> List of profiles </h3>")
        children = [Profile(p) for p in manage.get_config().profiles]
        super().__init__(children=[text, *children])
