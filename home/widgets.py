# -*- coding: utf-8 -*-
"""AiiDA lab basic widgets."""

from threading import Timer

import traitlets
import ipywidgets as ipw

from .themes import ThemeDefault as Theme


class _StatusWidgetMixin(traitlets.HasTraits):
    """Show temporary messages for example for status updates.

    This is a mixin class that is meant to be part of an inheritance
    tree of an actual widget with a 'value' traitlet that is used
    to convey a status message. See the non-private classes below
    for examples.
    """

    message = traitlets.Unicode(default_value='', allow_none=True)

    def __init__(self, *args, **kwargs):
        self._clear_timer = None
        super().__init__(*args, **kwargs)

    def _clear_value(self):
        """Set widget .value to be an empty string."""
        self.value = '' if self.message is None else self.message

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
    @traitlets.observe('message')
    def _observe_message(self, change):
        self.show_temporary_message(change['new'])


class StatusHTML(_StatusWidgetMixin, ipw.HTML):
    """Show temporary HTML messages for example for status updates."""

    # This method should be part of _StatusWidgetMixin, but that does not work
    # for an unknown reason.
    @traitlets.observe('message')
    def _observe_message(self, change):
        self.show_temporary_message(change['new'])


class AppStatusInfoWidget(ipw.HTML):
    """Widget that indicates whether an update is available."""

    detached = traitlets.Bool(allow_none=True)
    compatible = traitlets.Bool(allow_none=True)
    updates_available = traitlets.Bool(allow_none=True)

    MESSAGE_INIT = \
            f'<div>{Theme.ICONS.LOADING} Loading...</div>'

    TOOLTIP_DETACHED = \
            'The app is in a detached state - likely due to local modifications - '\
            'which means the ability to manage the app via the AiiDAlab interface is reduced.'

    MESSAGE_DETACHED = \
            f'<div title="{TOOLTIP_DETACHED}"><font color="{Theme.COLORS.GRAY}">'\
            f'{Theme.ICONS.APP_DETACHED} Modified</font></div>'

    TOOLTIP_APP_INCOMPATIBLE = \
            "None of the available app versions support the current AiiDAlab environment. "\
            "You can continue using this app, but be advised that you might encounter "\
            "compatibility issues."

    MESSAGE_APP_INCOMPATIBLE = \
            f'<div title="{TOOLTIP_APP_INCOMPATIBLE}"><font color="{Theme.COLORS.DANGER}">'\
            f'{Theme.ICONS.APP_INCOMPATIBLE} App incompatible</font></div>'

    TOOLTIP_APP_VERSION_INCOMPATIBLE = \
            'The currently installed version of this app is not supported for this '\
            'AiiDAlab environment. Click on &quot;Manage App&quot; to install a supported version '\
            'and avoid compatibility isssues.'

    MESSAGE_APP_VERSION_INCOMPATIBLE = \
            f'<div title="{TOOLTIP_APP_VERSION_INCOMPATIBLE}"><font color="{Theme.COLORS.WARNING}">'\
            f'{Theme.ICONS.APP_VERSION_INCOMPATIBLE} Update required</font></div>'

    MESSAGES_UPDATES = {
        None:
            '<div title="Encountered unknown problem while trying to determine whether '
            'updates are available for this app.>'\
            f'<font color="{Theme.COLORS.WARNING}">{Theme.ICONS.APP_UPDATE_AVAILABLE_UNKNOWN} '\
            'Unable to determine availability of updates.</font></div>',
        True:
            '<div title="Click on &quot;Manage app&quot; to install a newer version of this app.">'\
            f'<font color="{Theme.COLORS.NOTIFY}">{Theme.ICONS.APP_UPDATE_AVAILABLE} Update available</font></div>',
        False:
            '<div title="The currently installed version of this app is the latest available version.">'\
            f'<font color="{Theme.COLORS.CHECK}">{Theme.ICONS.APP_NO_UPDATE_AVAILABLE} Latest version</font></div>',
    }


    def __init__(self, value=None, **kwargs):
        if value is None:
            value = self.MESSAGE_INIT
        super().__init__(value=value, **kwargs)
        self.observe(self._refresh, names=['detached', 'compatible', 'updates_available'])

    def _refresh(self, _=None):
        if self.detached is True:
            self.value = self.MESSAGE_DETACHED
        elif self.compatible is False:
            if self.updates_available:
                self.value = self.MESSAGE_APP_VERSION_INCOMPATIBLE
            else:
                self.value = self.MESSAGE_APP_INCOMPATIBLE
        else:
            self.value = self.MESSAGES_UPDATES[self.updates_available]


class Spinner(ipw.HTML):
    """Widget that shows a simple spinner if enabled."""

    enabled = traitlets.Bool()

    def __init__(self, spinner_style=None):
        self.spinner_style = f' style="{spinner_style}"' if spinner_style else ''
        super().__init__()

    @traitlets.default('enabled')
    def _default_enabled(self):  # pylint: disable=no-self-use
        return False

    @traitlets.observe('enabled')
    def _observe_enabled(self, change):
        """Show spinner if enabled, otherwise nothing."""
        if change['new']:
            self.value = f"""<i class="fa fa-spinner fa-pulse"{self.spinner_style}></i>"""
        else:
            self.value = ""
