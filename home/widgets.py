"""AiiDAlab basic widgets."""

from threading import Timer

import ipywidgets as ipw
import traitlets
from aiidalab.app import AppRemoteUpdateStatus as AppStatus
from aiidalab.config import AIIDALAB_REGISTRY

from .themes import ThemeDefault as Theme


class _StatusWidgetMixin(traitlets.HasTraits):
    """Show temporary messages for example for status updates.

    This is a mixin class that is meant to be part of an inheritance
    tree of an actual widget with a 'value' traitlet that is used
    to convey a status message. See the non-private classes below
    for examples.
    """

    message = traitlets.Unicode(default_value="", allow_none=True)

    def __init__(self, *args, **kwargs):
        self._clear_timer = None
        super().__init__(*args, **kwargs)

    def _clear_value(self):
        """Set widget .value to be an empty string."""
        self.value = "" if self.message is None else self.message

    def show_temporary_message(self, value, clear_after=3):
        """Show a temporary message and clear it after the given interval."""
        if self._clear_timer is not None:
            # Cancel previous timer; has no effect if it already timed out.
            self._clear_timer.cancel()

        self.value = value

        # Start new timer that will clear the value after the specified interval.
        self._clear_timer = Timer(clear_after, self._clear_value)
        self._clear_timer.start()


class StatusLabel(_StatusWidgetMixin, ipw.Label):
    """Show temporary messages for example for status updates."""

    # This method should be part of _StatusWidgetMixin, but that does not work
    # for an unknown reason.
    @traitlets.observe("message")
    def _observe_message(self, change):
        self.show_temporary_message(change["new"])


class StatusHTML(_StatusWidgetMixin, ipw.HTML):
    """Show temporary HTML messages for example for status updates."""

    # This method should be part of _StatusWidgetMixin, but that does not work
    # for an unknown reason.
    @traitlets.observe("message")
    def _observe_message(self, change):
        self.show_temporary_message(change["new"])


class AppStatusInfoWidget(ipw.HTML):
    """Widget that indicates information about the app's installation status.

    The indicated information includes:

        - whether the app is detached (e.g. via local modifications)
        - whether the installed version and/or any version is compatible
        - there are updates available for installation
        - an update is required
    """

    detached = traitlets.Bool(allow_none=True)
    compatible = traitlets.Bool(allow_none=True)
    remote_update_status = traitlets.UseEnum(AppStatus)

    MESSAGE_INIT = f"<div>{Theme.ICONS.LOADING} Loading...</div>"

    TOOLTIP_APP_DETACHED = (
        "The app is in a detached state - likely due to local modifications - "
        "which means the ability to manage the app via the AiiDAlab interface is reduced."
    )

    TOOLTIP_APP_INCOMPATIBLE = (
        "None of the available app versions are compatible with this AiiDAlab environment. "
        "You can continue using this app, but be advised that you might encounter "
        "compatibility issues."
    )

    TOOLTIP_APP_NOT_REGISTERED = "This app is not registered."

    MESSAGE_APP_INCOMPATIBLE = (
        f'<div title="{TOOLTIP_APP_INCOMPATIBLE}"><font color="{Theme.COLORS.DANGER}">'
        f"{Theme.ICONS.APP_INCOMPATIBLE} App incompatible</font></div>"
    )

    TOOLTIP_APP_VERSION_INCOMPATIBLE = (
        "The currently installed version of this app is not compatible with this "
        "AiiDAlab environment. Click on &quot;Manage App&quot; to install a compatible version "
        "and avoid compatibility isssues."
    )

    MESSAGES_UPDATES = {  # noqa: RUF012
        AppStatus.CANNOT_REACH_REGISTRY: f'<div title="Unable to reach the registry server ({AIIDALAB_REGISTRY}).">'
        f'<font color="{Theme.COLORS.GRAY}">{Theme.ICONS.APP_UPDATE_AVAILABLE_UNKNOWN} '
        "Cannot reach server.</font></div>",
        AppStatus.UPDATE_AVAILABLE: '<div title="Click on &quot;Manage app&quot; to install a newer version of this app.">'
        f'<font color="{Theme.COLORS.NOTIFY}">{Theme.ICONS.APP_UPDATE_AVAILABLE} Update available</font></div>',
        AppStatus.UP_TO_DATE: '<div title="The currently installed version of this app is the latest available version.">'
        f'<font color="{Theme.COLORS.CHECK}">{Theme.ICONS.APP_NO_UPDATE_AVAILABLE} Latest version</font></div>',
        AppStatus.DETACHED: f'<div title="{TOOLTIP_APP_DETACHED}"><font color="{Theme.COLORS.GRAY}">'
        f"{Theme.ICONS.APP_DETACHED} Modified</font></div>",
        AppStatus.NOT_REGISTERED: f'<div title="{TOOLTIP_APP_NOT_REGISTERED}"><font color="{Theme.COLORS.GRAY}">'
        f"{Theme.ICONS.APP_NOT_REGISTERED} Not registered</font></div>",
    }

    def __init__(self, value=None, **kwargs):
        if value is None:
            value = self.MESSAGE_INIT
        super().__init__(value=value, **kwargs)
        self.observe(
            self._refresh, names=["detached", "compatible", "remote_update_status"]
        )

    def _refresh(self, _=None):
        if self.compatible is False:
            self.value = self.MESSAGE_APP_INCOMPATIBLE
        else:
            self.value = self.MESSAGES_UPDATES[self.remote_update_status]


class Spinner(ipw.HTML):
    """Widget that shows a simple spinner if enabled."""

    enabled = traitlets.Bool()

    def __init__(self, spinner_style=None):
        self.spinner_style = f' style="{spinner_style}"' if spinner_style else ""
        super().__init__()

    @traitlets.default("enabled")
    def _default_enabled(self):
        return False

    @traitlets.observe("enabled")
    def _observe_enabled(self, change):
        """Show spinner if enabled, otherwise nothing."""
        if change["new"]:
            self.value = (
                f"""<i class="fa fa-spinner fa-pulse"{self.spinner_style}></i>"""
            )
        else:
            self.value = ""


class LogOutputWidget(ipw.VBox):
    value = traitlets.Unicode()
    template = traitlets.Unicode()

    def __init__(self, **kwargs):
        self._output = ipw.HTML(layout=ipw.Layout(min_width="60em"))
        self._refresh_output()
        super().__init__(
            children=[
                self._output,
            ],
            **kwargs,
        )

    def write(self, text):
        self.value += text

    @traitlets.default("value")
    def _default_value(self):
        return ""

    @traitlets.default("template")
    def _default_template(self):
        return """<pre style="background-color: #1f1f2e; color: white; line-height: 100%">{text}</pre>"""

    @traitlets.observe("value")
    def _refresh_output(self, _=None):
        with self.hold_trait_notifications():
            self._output.value = (
                self.template.format(text=self.value) if self.value else ""
            )
