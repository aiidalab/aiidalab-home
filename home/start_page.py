"""Module to generate AiiDAlab home page."""

import json
from functools import wraps
from glob import glob
from os import path

import ipywidgets as ipw
import traitlets
from aiidalab.app import AiidaLabApp
from aiidalab.config import AIIDALAB_APPS
from IPython.display import display

from home.utils import load_widget
from home.widgets import AppStatusInfoWidget


def create_app_widget_move_buttons(name):
    """Make buttons to move the app widget up or down."""
    layout = ipw.Layout(width="40px")
    app_widget_move_buttons = ipw.HTML(
        f"""
    <a href=./start.ipynb?move_up={name} title="Move it up"><i class='fa fa-arrow-up' style='color:#337ab7;font-size:2em;' ></i></a>
    <a href=./start.ipynb?move_down={name} title="Move it down"><i class='fa fa-arrow-down' style='color:#337ab7;font-size:2em;' ></i></a>
    """,
        layout=layout,
    )
    app_widget_move_buttons.layout.margin = "50px 0px 0px 0px"

    return app_widget_move_buttons


def _workaround_property_lock_issue(func):
    """Work-around for issue with the ipw.Accordion widget.

    The widget does not report changes to the .selected_index trait when displayed
    within a custom ipw.Output instance. However, the change is somewhat cryptic reported
    by a change to the private '_property_lock' trait. We observe changes to that trait
    and convert the change argument into a form that is more like the one expected by
    downstream handlers.
    """

    @wraps(func)
    def _inner(self, change):
        if change["name"] == "_property_lock":
            if "selected_index" in change["old"]:
                fixed_change = change.copy()
                fixed_change["name"] = "selected_index"
                fixed_change["new"] = change["old"]["selected_index"]
                del fixed_change["old"]
                return func(self, fixed_change)

        return func(self, change)

    return _inner


class AiidaLabHome:
    """Class that mananges the appearance of the AiiDAlab home page."""

    def __init__(self, is_demo_server=False):
        self.config_fn = ".launcher.json"
        self.is_demo_server = is_demo_server
        self._app_widgets = {}

    def _create_app_widget(self, name):
        """Create the widget representing the app on the home screen."""
        config = self.read_config()
        app = AiidaLabApp(name, None, AIIDALAB_APPS)
        app_widget = CollapsableAppWidget(app, allow_move=True)
        app_widget.hidden = name in config["hidden"]
        app_widget.observe(self._on_app_widget_change_hidden, names=["hidden"])
        return app_widget

    def _create_home_widget(self):
        """Create the home app widget."""
        app = AiidaLabApp("home", None, AIIDALAB_APPS)
        return AppWidget(app, allow_move=False, allow_manage=False)

    def _on_app_widget_change_hidden(self, change):
        """Record whether a app widget is hidden on the home screen in the config file."""
        config = self.read_config()
        hidden = set(config["hidden"])
        if change["new"]:  # hidden
            hidden.add(change["owner"].app.name)
        else:  # visible
            hidden.discard(change["owner"].app.name)
        config["hidden"] = list(hidden)
        self.write_config(config)

    def write_config(self, config):
        json.dump(config, open(self.config_fn, "w"), indent=2)

    def read_config(self):
        if path.exists(self.config_fn):
            return json.load(open(self.config_fn))
        return {"order": [], "hidden": []}  # default config

    def render(self):
        """Rendering all apps."""
        home = self._create_home_widget()
        display(home)

        if self.is_demo_server:
            display(ipw.HTML("<h1>Welcome to the AiiDAlab demo server</h1>"))
            intro_content = ipw.HTML("""
<p>
    This is a demo server for AiiDAlab, a platform for computational science based on the AiiDA framework. The server is running on a virtual machine with limited resources and is intended for demonstration purposes only. The server is reset to its initial state every 24 hours. For more information on AiiDAlab, please visit the <a href="https://www.aiidalab.net" target="_blank">AiiDAlab website</a>.
</p>
<h4>Instructions:</h4>
<ul>
    <li>To <strong>launch the app</strong>, click the app logo</li>
    <li>To manage the app, click on the "Manage App" button</li>
    <li>To view the app's codebase, click on the "URL" button</li>
</ul>
            """)
            intro = ipw.VBox([intro_content])
            intro.add_class("intro-message")
            display(intro)

        for name in self.load_apps():
            # Create app widget if it has not been created yet.
            if name not in self._app_widgets:
                self._app_widgets[name] = self._create_app_widget(name)
            display(self._app_widgets[name])

    def load_apps(self):
        """Load apps according to the order defined in the config file."""
        apps = [
            path.basename(fn)
            for fn in glob(path.join(AIIDALAB_APPS, "*"))
            if path.isdir(fn)
            and not fn.endswith("home")
            and not fn.endswith("__pycache__")
        ]
        config = self.read_config()
        order = config["order"]
        apps.sort(key=lambda x: order.index(x) if x in order else -1)
        config["order"] = apps
        self.write_config(config)
        return apps

    def move_updown(self, name, delta):
        """Move the app up/down on the start page."""
        config = self.read_config()
        order = config["order"]
        i = order.index(name)
        del order[i]
        j = min(len(order), max(0, i + delta))
        order.insert(j, name)
        config["order"] = order
        self.write_config(config)


class AppWidget(ipw.VBox):
    """Widget that represents an app as part of the home page."""

    def __init__(self, app, allow_move=False, allow_manage=True):
        self.app = app

        launcher = load_widget(app.name)
        launcher.layout.flex = "1"

        header_items = []
        footer_items = []

        if allow_manage:
            app_status_info = AppStatusInfoWidget()
            for trait in ("detached", "compatible", "remote_update_status"):
                ipw.dlink((app, trait), (app_status_info, trait))
            app_status_info.layout.margin = "0px 0px 0px auto"
            header_items.append(app_status_info)

            footer_items.append(
                f"""<a href=./single_app.ipynb?app={app.name} target="_blank"><button>Manage App</button></a>"""
            )
            if app.metadata.get("external_url"):
                footer_items.append(
                    f"""<a href="{app.metadata['external_url']}" target="_blank"><button>URL</button></a>"""
                )

        if allow_move:
            app_widget_move_buttons = create_app_widget_move_buttons(app.name)
            body = ipw.HBox([launcher, app_widget_move_buttons])
        else:
            body = launcher

        header = ipw.HBox(header_items)
        header.layout.margin = None if allow_manage else "20px 0px 0px 0px"

        footer = ipw.HTML(" ".join(footer_items), layout={"width": "initial"})
        footer.layout.margin = (
            "0px 0px 0px auto" if allow_manage else "0px 0px 20px 0px"
        )

        super().__init__(children=[header, body, footer])


class CollapsableAppWidget(ipw.Accordion):
    """Widget that represents a collapsable app as part of the home page."""

    hidden = traitlets.Bool()

    def __init__(self, app, **kwargs):
        self.app = app
        app_widget = AppWidget(app, **kwargs)
        super().__init__(children=[app_widget])
        self.set_title(0, app.title)
        # Need to observe all names here due to unidentified issue:
        self.observe(
            self._observe_accordion_selected_index
        )  # , names=['selected_index'])

    @_workaround_property_lock_issue
    def _observe_accordion_selected_index(self, change):
        if (
            change["name"] == "selected_index"
        ):  # Observing all names due to unidentified issue.
            self.hidden = change["new"] is None

    @traitlets.observe("hidden")
    def _observe_hidden(self, change):
        self.selected_index = None if change["new"] else 0
