# -*- coding: utf-8 -*-
"""Module that contains widgets for managing AiiDAlab applications."""
from subprocess import CalledProcessError

import ipywidgets as ipw
import traitlets
from aiidalab.app import AppRemoteUpdateStatus as AppStatus
from aiidalab.app import AppVersion
from jinja2 import Template
from packaging.version import parse

from home.utils import load_logo
from home.widgets import LogOutputWidget, Spinner, StatusHTML

HTML_MSG_PROGRESS = """{}"""

HTML_MSG_SUCCESS = """<i class="fa fa-check" style="color:#337ab7;font-size:1em;" ></i>
{}"""

HTML_MSG_FAILURE = """<i class="fa fa-times" style="color:red;font-size:1em;" ></i>
{}"""


class HeaderWarning(ipw.HTML):
    """Class to display a warning in the header."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.layout = ipw.Layout(
            display="none",
            width="600px",
            height="auto",
            margin="0px 0px 0px 0px",
            padding="0px 0px 0px 0px",
        )

    def show(self, message):
        """Show the warning."""
        self.value = f"""<div class="alert alert-danger" role="alert">{message}</div>"""
        self.layout.display = "block"

    def hide(self):
        """Hide the warning."""
        self.layout.display = "none"


class VersionSelectorWidget(ipw.VBox):
    """Class to choose app's version."""

    disabled = traitlets.Bool()
    prereleases = traitlets.Bool()

    def __init__(self, *args, **kwargs):
        style = {"description_width": "100px"}
        self.version_to_install = ipw.Dropdown(
            description="Install version",
            disabled=True,
            style=style,
        )
        self.installed_version = ipw.Text(
            description="Installed version",
            disabled=True,
            style=style,
        )
        self.info = StatusHTML(
            value="",
            layout={"max_width": "600px"},
            style=style,
        )

        super().__init__(
            children=[self.installed_version, self.version_to_install, self.info],
            layout={"min_width": "300px"},
            *args,
            **kwargs,
        )

    @traitlets.observe("disabled")
    def _observe_disabled(self, change):
        self.version_to_install.disabled = change["new"]


class AppManagerWidget(ipw.VBox):
    """Widget for management of apps.

    Shows basic information about the app (description, authors, etc.) and provides
    an interface to install, uninstall, and update the application, as well as change
    versions if possible.
    """

    COMPATIBILITY_INFO = Template(
        """<div class="alert alert-warning alert-dismissible">
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

            The compatibility issues may be caused by other installed apps. To solve this, you can re-install this app, and uninstall the app that caused the conflict.
        </div>"""
    )

    DEPENDENCIES_INSTALL_INFO = Template(
        """<div class="alert alert-info alert-dismissible">
        <a href="#" class="close" data-dismiss="alert" aria-label="close">&times;</a>
            The following dependencies will be changed:
            <ul>
            {% for p in dependencies_to_install %}
                <li> {{ p.installed if p.installed is not none else '[Not Installed]' }} --> {{ p.required }} </li>
            {% endfor %}
            </ul>

            WARNING: Reinstalling previously installed dependencies may break already installed apps. If that happens, uninstall this app and reinstall the app that was broken.
        </div>"""
    )

    TEMPLATE = Template(
        """<b> <div style="font-size: 30px; text-align:center;">{{ app.title }}</div></b>
    <br>
    <b>Authors:</b> {{ app.authors }}
    <br>
    <b>Description:</b> {{ app.description }}
    {% if app.url %}
    <br>
    <b>URL:</b> <a href="{{ app.url }}">{{ app.url }}</a>
    {% endif %}"""
    )

    def __init__(self, app, minimalistic=False):
        self.app = app

        self.header_warning = HeaderWarning()

        body = ipw.HTML(self.TEMPLATE.render(app=app))
        body.layout = {"width": "600px"}

        # Setup install_info
        self.install_info = StatusHTML(
            message="<p><br></p>"
        )  # show empty line by default

        self.dependencies_log = LogOutputWidget(
            layout=ipw.Layout(min_height="0px", max_height="100px")
        )  # max_height controls the maximum height of the log field.
        self.dependencies_log.template = (
            "Installing dependencies..." + self.dependencies_log.template
        )

        # Setup buttons
        self.install_button = ipw.Button(description="Install", disabled=True)
        self.install_button.on_click(self._install_version)

        self.uninstall_button = ipw.Button(description="Uninstall", disabled=True)
        self.uninstall_button.on_click(self._uninstall_app)

        self.update_button = ipw.Button(description="Update", disabled=True)
        self.update_button.on_click(self._update_app)

        self.issue_indicator = ipw.HTML()
        self.blocked_ignore = ipw.Checkbox(description="Ignore")
        self.blocked_ignore.observe(self._refresh_widget_state)

        self.compatibility_info = ipw.HTML()
        self.dependencies_install_info = ipw.HTML()

        self.spinner = Spinner("color:#337ab7;font-size:1em;")
        ipw.dlink((self.app, "busy"), (self.spinner, "enabled"))

        self.version_selector = VersionSelectorWidget()
        ipw.dlink(
            (self.app, "available_versions"),
            (self.version_selector.version_to_install, "options"),
            transform=lambda versions: [
                (self._formatted_version(version), version) for version in versions
            ],
        )
        ipw.dlink(
            (self.app, "installed_version"),
            (self.version_selector.installed_version, "value"),
            transform=self._formatted_version,
        )
        ipw.dlink(
            (self.version_selector.version_to_install, "value"),
            (self.app, "version_to_install"),
        )

        self.version_selector.disabled = True
        self.version_selector.version_to_install.observe(
            self._refresh_widget_state, "value"
        )

        # Prereleases opt-in
        self.include_prereleases = ipw.Checkbox(description="Include prereleases")
        ipw.dlink(
            (self.include_prereleases, "value"), (self.app, "include_prereleases")
        )
        self.app.observe(
            self._refresh_prereleases, names=["has_prereleases", "installed_version"]
        )
        self._refresh_prereleases(change=dict(owner=self.app))  # initialize

        children = [
            ipw.HBox([self.header_warning]),
            ipw.HBox([load_logo(app), body]),
            self.version_selector if not minimalistic else ipw.Box(),
            self.include_prereleases,
            ipw.HBox(
                [
                    self.uninstall_button,
                    self.install_button,
                    self.update_button,
                    self.spinner,
                ]
            ),
            ipw.HBox([self.install_info]),
            ipw.HBox([self.dependencies_log]),
            ipw.HBox([self.issue_indicator, self.blocked_ignore]),
        ]
        if not minimalistic:
            children.extend(
                [
                    ipw.HBox([self.compatibility_info]),
                    ipw.HBox([self.dependencies_install_info]),
                ]
            )

        super().__init__(children=children)

        self.app.observe(self._refresh_widget_state)
        self.app.refresh_async()  # init all widgets

    @staticmethod
    def _formatted_version(version):
        """Format the unambigious version identifiee to a human-friendly representation."""
        if version is AppVersion.NOT_INSTALLED:
            return "[not installed]"

        if version is AppVersion.UNKNOWN:
            return "[unknown version]"

        if not version:  # will be displayed during transition phases
            return "[n/a]"

        return version

    def _refresh_prereleases(self, change):
        app = change["owner"]
        installed_version = app.installed_version

        has_prereleases = app.has_prereleases
        prerelease_installed = (
            parse(installed_version).is_prerelease
            if isinstance(installed_version, str)
            else False
        )

        with self.hold_trait_notifications():
            # The checkbox can only be enabled when the app has prereleases,
            # and cannot be disabled when a prerelease is currently installed.
            self.include_prereleases.disabled = (
                prerelease_installed or not has_prereleases
            )
            # The checkbox is checked if it was already checked or a prerelease is installed:
            self.include_prereleases.value = (
                self.include_prereleases.value or prerelease_installed
            )

    def _refresh_widget_state(self, _=None):
        """Refresh the widget to reflect the current state of the app."""
        with self.hold_trait_notifications():
            # Collect information about app state.
            installed = self.app.is_installed()
            installed_version = self.app.installed_version
            version_to_install = self.app.version_to_install
            dependencies_to_install = self.app.dependencies_to_install
            registered = self.app.remote_update_status is not AppStatus.NOT_REGISTERED
            cannot_reach_registry = (
                self.app.remote_update_status is AppStatus.CANNOT_REACH_REGISTRY
            )
            busy = self.app.busy
            detached = self.app.detached
            available_versions = self.app.available_versions
            compatible = (
                len(available_versions) > 0
            )  # Compatibility of the app, not the version: self.app.compatible.

            override = detached and self.blocked_ignore.value
            blocked_install = (
                detached or not compatible
            ) and not self.blocked_ignore.value
            blocked_uninstall = (
                detached or not registered or cannot_reach_registry
            ) and not self.blocked_ignore.value

            # Check the compatibility of current installed version and show banner if not compatible.
            if not busy and installed and not self.app.compatible:
                self.header_warning.show(
                    "The installed version of this app is not compatible with this AiiDAlab environment."
                )
            elif not busy and not installed and not available_versions:
                self.header_warning.show(
                    f"No version of <b>{self.app.title}</b> compatible with the current AiiDAlab environment found."
                )
            else:
                self.header_warning.hide()

            # Prepare warning icons and messages depending on whether we override or not.
            # These messages and icons are only shown if needed.
            warn_or_ban_icon = "warning" if override else "ban"
            if override:
                tooltip_danger = "Operation will lead to potential loss of local data!"
            else:
                tooltip_danger = "Operation blocked due to potential data loss."
            tooltip_incompatible = "The app is not supported for this environment."

            # Determine whether the app can be installed, updated, and uninstalled.
            can_switch = bool(
                installed_version != version_to_install and available_versions
            )
            latest_selected = self.version_selector.version_to_install.index == 0
            can_install = bool(
                can_switch and (detached or not latest_selected)
            ) or bool(not installed and available_versions)
            can_uninstall = installed
            try:
                can_update = (
                    self.app.remote_update_status is AppStatus.UPDATE_AVAILABLE
                    and installed
                )
            except RuntimeError:
                can_update = None

            # Update the install button state.
            disable_install_button = busy or blocked_install or not can_install
            self.install_button.disabled = disable_install_button
            self.install_button.button_style = "info" if can_install else ""
            self.install_button.icon = (
                ""
                if can_install and not detached
                else warn_or_ban_icon
                if can_install
                else ""
            )
            if self.app.compatible:
                self.install_button.tooltip = (
                    ""
                    if can_install and not detached
                    else tooltip_danger
                    if can_install
                    else ""
                )
            else:
                self.install_button.tooltip = (
                    ""
                    if installed and not detached
                    else tooltip_danger
                    if installed
                    else tooltip_incompatible
                )
            self.install_button.description = (
                "Install"
                if disable_install_button
                else f"Install ({self._formatted_version(version_to_install)})"
            )

            # Update the uninstall button state.
            self.uninstall_button.disabled = (
                busy or blocked_uninstall or not can_uninstall
            )
            self.uninstall_button.button_style = "danger" if can_uninstall else ""
            self.uninstall_button.icon = warn_or_ban_icon if detached else "trash-o"
            self.uninstall_button.tooltip = (
                ""
                if can_uninstall and not detached
                else tooltip_danger
                if can_uninstall
                else ""
            )

            # Update the update button state.
            self.update_button.disabled = busy or blocked_install or not can_update
            if installed and can_update is None:
                self.update_button.icon = "warning"
                self.update_button.tooltip = (
                    "Unable to determine availability of updates."
                )
            else:
                self.update_button.icon = (
                    "arrow-circle-up"
                    if can_update and not detached
                    else warn_or_ban_icon
                    if can_update
                    else ""
                )
                self.update_button.button_style = "success" if can_update else ""
                self.update_button.tooltip = (
                    ""
                    if can_update and not detached
                    else tooltip_danger
                    if can_update
                    else ""
                )

            # Update the version_selector widget state.
            more_than_one_version = (
                len(self.version_selector.version_to_install.options) > 1
            )
            self.version_selector.disabled = (
                busy or blocked_install or not more_than_one_version
            )

            # Indicate whether there are local modifications and present option for user override.
            if cannot_reach_registry:
                self.issue_indicator.value = f'<i class="fa fa-{warn_or_ban_icon}"></i> Unable to reach the registry server.'
            elif not registered:
                self.issue_indicator.value = f'<i class="fa fa-{warn_or_ban_icon}"></i> The app is not registered.'
            elif busy:
                self.issue_indicator.value = ""
            elif detached:
                self.issue_indicator.value = (
                    f'<i class="fa fa-{warn_or_ban_icon}"></i> The app has local modifications or was checked out '
                    "to an unknown version."
                )
            else:
                self.issue_indicator.value = ""
            self.blocked_ignore.layout.visibility = (
                "visible" if (detached or not compatible) else "hidden"
            )

            if (
                not busy
                and any(self.app.compatibility_info.values())
                and not self.app.compatible
            ):
                self.compatibility_info.value = self.COMPATIBILITY_INFO.render(
                    app=self.app
                )
            else:
                self.compatibility_info.value = ""

            # Check and show the dependencies install infos
            if not busy and can_switch and any(dependencies_to_install):
                self.dependencies_install_info.value = (
                    self.DEPENDENCIES_INSTALL_INFO.render(
                        dependencies_to_install=dependencies_to_install,
                    )
                )
            else:
                self.dependencies_install_info.value = ""

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
            raise RuntimeError(
                "Unable to perform operation, the app is in a detached state."
            )

    def _install_version(self, _=None):
        """Attempt to install the a specific version of the app."""
        version = self.version_selector.version_to_install.value  # can be None
        try:
            self._check_detached_state()
            version = self.app.install_app(
                version=version, stdout=self.dependencies_log
            )  # argument may be None
        except (AssertionError, RuntimeError, CalledProcessError) as error:
            self._show_msg_failure(str(error))
        else:
            self._show_msg_success(
                f"Installed app ({self._formatted_version(version)})."
            )
            self.dependencies_log.value = ""

    def _update_app(self, _):
        """Attempt to update the app."""
        try:
            self._check_detached_state()
            self.app.update_app(stdout=self.dependencies_log)
        except (AssertionError, RuntimeError, CalledProcessError) as error:
            self._show_msg_failure(str(error))
        else:
            self._show_msg_success("Updated app.")
            self.dependencies_log.value = ""

    def _uninstall_app(self, _):
        """Attempt to uninstall the app."""
        try:
            self._check_detached_state()
            self.app.uninstall_app()
        except RuntimeError as error:
            self._show_msg_failure(str(error))
        else:
            self._show_msg_success("Uninstalled app.")
