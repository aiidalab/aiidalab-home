# -*- coding: utf-8 -*-
"""Module that contains widgets for managing AiiDAlab applications."""

import re
from subprocess import CalledProcessError

import traitlets
import ipywidgets as ipw
from jinja2 import Template
from aiidalab.app import AppVersion

from home.widgets import StatusHTML, Spinner
from home.utils import load_logo

HTML_MSG_PROGRESS = """{}"""

HTML_MSG_SUCCESS = """<i class="fa fa-check" style="color:#337ab7;font-size:1em;" ></i>
{}"""

HTML_MSG_FAILURE = """<i class="fa fa-times" style="color:red;font-size:1em;" ></i>
{}"""


class VersionSelectorWidget(ipw.VBox):
    """Class to choose app's version."""

    disabled = traitlets.Bool()

    def __init__(self, *args, **kwargs):
        style = {'description_width': '100px'}
        self.version_to_install = ipw.Dropdown(
            description='Install version',
            disabled=True,
            style=style,
        )
        self.installed_version = ipw.Text(
            description='Installed version',
            disabled=True,
            style=style,
        )
        self.info = StatusHTML(
            value='',
            layout={'max_width': '600px'},
            style=style,
        )

        super().__init__(
            children=[self.installed_version, self.version_to_install, self.info],
            layout={'min_width': '300px'},
            *args,
            **kwargs,
        )

    @traitlets.observe('disabled')
    def _observe_disabled(self, change):
        self.version_to_install.disabled = change['new']


class AppManagerWidget(ipw.VBox):
    """Widget for management of apps.

    Shows basic information about the app (description, authors, etc.) and provides
    an interface to install, uninstall, and update the application, as well as change
    versions if possible.
    """

    COMPATIBILTIY_WARNING = Template("""<div class="alert alert-danger">
    The installed version of this app is not compatible with this AiiDAlab environment.
    </div>""")

    COMPATIBILITY_INFO = Template("""<div class="alert alert-warning alert-dismissible">
        <a href="#" class="close" data-dismiss="alert" aria-label="close">&times;</a>
            Reasons for incompatibility:
            <ul>
            {% for spec in app.compatibility_info %}
                <li>{{ spec }}:
                    <ul>
                        {% for missing_req in app.compatibility_info[spec] %}
                        <li>missing: {{ missing_req }}</li>
                        {% endfor %}
                    </ul>
                </li>
            {% endfor %}
            </ul>
        </div>""")

    TEMPLATE = Template("""<b> <div style="font-size: 30px; text-align:center;">{{ app.title }}</div></b>
    <br>
    <b>Authors:</b> {{ app.authors }}
    <br>
    <b>Description:</b> {{ app.description }}
    {% if app.url %}
    <br>
    <b>URL:</b> <a href="{{ app.url }}">{{ app.url }}</a>
    {% endif %}""")

    def __init__(self, app, with_version_selector=False):
        self.app = app

        self.compatibility_warning = ipw.HTML(self.COMPATIBILTIY_WARNING.render())
        self.compatibility_warning.layout = {'width': '600px'}
        self.compatibility_warning.layout.visibility = 'hidden'

        body = ipw.HTML(self.TEMPLATE.render(app=app))
        body.layout = {'width': '600px'}

        # Setup install_info
        self.install_info = StatusHTML(message="<p><br></p>")  # show empty line by default

        # Setup buttons
        self.install_button = ipw.Button(description='Install', disabled=True)
        self.install_button.on_click(self._install_version)

        self.uninstall_button = ipw.Button(description='Uninstall', disabled=True)
        self.uninstall_button.on_click(self._uninstall_app)

        self.update_button = ipw.Button(description='Update', disabled=True)
        self.update_button.on_click(self._update_app)

        self.issue_indicator = ipw.HTML()
        self.blocked_ignore = ipw.Checkbox(description="Ignore")
        self.blocked_ignore.observe(self._refresh_widget_state)

        self.compatibility_info = ipw.HTML()

        self.spinner = Spinner("color:#337ab7;font-size:1em;")
        ipw.dlink((self.app, 'busy'), (self.spinner, 'enabled'))

        children = [
            ipw.HBox([self.compatibility_warning]),
            ipw.HBox([load_logo(app), body]),
            ipw.HBox([self.uninstall_button, self.install_button, self.update_button, self.spinner]),
            ipw.HBox([self.install_info]),
            ipw.HBox([self.issue_indicator, self.blocked_ignore]),
            ipw.HBox([self.compatibility_info]),
        ]

        self.version_selector = VersionSelectorWidget()
        ipw.dlink((self.app, 'available_versions'), (self.version_selector.version_to_install, 'options'),
                  transform=lambda versions: [(self._formatted_version(version), version) for version in versions])
        ipw.dlink((self.app, 'installed_version'), (self.version_selector.installed_version, 'value'),
                  transform=self._formatted_version)
        self.version_selector.layout.visibility = 'visible' if with_version_selector else 'hidden'
        self.version_selector.disabled = True
        self.version_selector.version_to_install.observe(self._refresh_widget_state, 'value')
        children.insert(2, self.version_selector)

        super().__init__(children=children)

        self.app.observe(self._refresh_widget_state)
        self.app.refresh_async()  # init all widgets

    @staticmethod
    def _formatted_version(version):
        """Format the unambigious version identifiee to a human-friendly representation."""
        if version is AppVersion.NOT_INSTALLED:
            return '[not installed]'

        if version is AppVersion.UNKNOWN:
            return '[unknown version]'

        if not version:  # will be displayed during transition phases
            return '[n/a]'

        return version

    def _refresh_widget_state(self, _=None):
        """Refresh the widget to reflect the current state of the app."""
        with self.hold_trait_notifications():
            # Collect information about app state.
            installed = self.app.is_installed()
            installed_version = self.app.installed_version
            compatible = len(self.app.available_versions) > 0
            busy = self.app.busy
            detached = self.app.detached
            available_versions = self.app.available_versions

            override = detached and self.blocked_ignore.value
            blocked_install = (detached or not compatible) and not self.blocked_ignore.value
            blocked_uninstall = detached and not self.blocked_ignore.value

            # Check app compatibility and show banner if not compatible.
            self.compatibility_warning.layout.visibility = \
                    'visible' if (self.app.is_installed() and self.app.compatible is False) else 'hidden'

            # Prepare warning icons and messages depending on whether we override or not.
            # These messages and icons are only shown if needed.
            warn_or_ban_icon = "warning" if override else "ban"
            if override:
                tooltip_danger = "Operation will lead to potential loss of local modifications!"
            else:
                tooltip_danger = "Operation blocked due to local modifications."
            tooltip_incompatible = "The app is not supported for this version of the environment."

            # Determine whether we can install, updated, and uninstall.
            can_switch = installed_version != self.version_selector.version_to_install.value and available_versions
            latest_selected = self.version_selector.version_to_install.index == 0
            can_install = (can_switch and (detached or not latest_selected)) or not installed
            can_uninstall = installed
            try:
                can_update = self.app.updates_available and installed
            except RuntimeError:
                can_update = None

            # Update the install button state.
            self.install_button.disabled = busy or blocked_install or not can_install
            self.install_button.button_style = 'info' if can_install else ''
            self.install_button.icon = '' if can_install and not detached else warn_or_ban_icon if can_install else ''
            if self.app.compatible:
                self.install_button.tooltip = '' if can_install and not detached else \
                        tooltip_danger if can_install else ''
            else:
                self.install_button.tooltip = '' if installed and not detached else \
                        tooltip_danger if installed else tooltip_incompatible
            self.install_button.description = 'Install' if not (installed and can_install) else \
                    f'Install ({self._formatted_version(self.version_selector.version_to_install.value)})'

            # Update the uninstall button state.
            self.uninstall_button.disabled = busy or blocked_uninstall or not can_uninstall
            self.uninstall_button.button_style = 'danger' if can_uninstall else ''
            self.uninstall_button.icon = warn_or_ban_icon if detached else 'trash-o'
            self.uninstall_button.tooltip = '' if can_uninstall and not detached else tooltip_danger if can_uninstall else ''

            # Update the update button state.
            self.update_button.disabled = busy or blocked_install or not can_update
            if self.app.is_installed() and can_update is None:
                self.update_button.icon = 'warning'
                self.update_button.tooltip = 'Unable to determine availability of updates.'
            else:
                self.update_button.icon = \
                    "arrow-circle-up" if can_update and not detached else warn_or_ban_icon if can_update else ""
                self.update_button.button_style = 'success' if can_update else ''
                self.update_button.tooltip = '' if can_update and not detached else tooltip_danger if can_update else ''

            # Update the version_selector widget state.
            more_than_one_version = len(self.version_selector.version_to_install.options) > 1
            self.version_selector.disabled = busy or blocked_install or not more_than_one_version

            # Indicate whether there are local modifications and present option for user override.
            if detached:
                self.issue_indicator.value = \
                    f'<i class="fa fa-{warn_or_ban_icon}"></i> The app is modified or the installed version '\
                    'is not on the specified release line.'
            elif not compatible:
                self.issue_indicator.value = \
                    f'<i class="fa fa-{warn_or_ban_icon}"></i> The app is not supported for this version of the environment.'
            else:
                self.issue_indicator.value = ''
            self.blocked_ignore.layout.visibility = 'visible' if (detached or not compatible) else 'hidden'

            if any(self.app.compatibility_info.values()) and self.app.compatible is False:
                self.compatibility_info.value = self.COMPATIBILITY_INFO.render(app=self.app)
            else:
                self.compatibility_info.value = ""

    def _show_msg_success(self, msg):
        """Show a message indicating successful execution of a requested operation."""
        self.install_info.show_temporary_message(HTML_MSG_SUCCESS.format(msg))

    def _show_msg_failure(self, msg):
        """Show a message indicating failure to execute a requested operation."""
        self.install_info.show_temporary_message(HTML_MSG_FAILURE.format(msg))

    def _check_detached_state(self):
        """Check whether the app is in a detached state which would prevent any install or other operations."""
        self.app.refresh()
        self._refresh_widget_state()
        blocked = self.app.detached and not self.blocked_ignore.value
        if blocked:
            raise RuntimeError("Unable to perform operation, the app is in a detached state.")

    def _install_version(self, _=None):
        """Attempt to install the a specific version of the app."""
        version = self.version_selector.version_to_install.value  # can be None
        try:
            self._check_detached_state()
            version = self.app.install_app(version=version)  # argument may be None
        except (AssertionError, RuntimeError, CalledProcessError) as error:
            self._show_msg_failure(str(error))
        else:
            self._show_msg_success(f"Installed app ({self._formatted_version(version)}).")

    def _update_app(self, _):
        """Attempt to update the app."""
        try:
            self._check_detached_state()
            version = self.app.update_app()
        except (AssertionError, RuntimeError, CalledProcessError) as error:
            self._show_msg_failure(str(error))
        else:
            self._show_msg_success("Updated app.")

    def _uninstall_app(self, _):
        """Attempt to uninstall the app."""
        try:
            self._check_detached_state()
            self.app.uninstall_app()
        except RuntimeError as error:
            self._show_msg_failure(str(error))
        else:
            self._show_msg_success("Uninstalled app.")
