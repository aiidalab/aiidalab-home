import html
import threading
from datetime import datetime

import ipywidgets as ipw

from home.themes import ThemeDefault as Theme

_STATE_COLORS = {
    "ok": Theme.COLORS.CHECK,
    "warning": Theme.COLORS.AIIDALAB_ORANGE,
    "error": Theme.COLORS.DANGER,
}


def _state_span(state, text) -> str:
    return f"<span style='color:{_STATE_COLORS[state]}'>{html.escape(text)}</span>"


class ControlSectionWidget(ipw.VBox):
    """Shared anatomy for a control-page tab: description, body, footer.

    The footer (refresh button, "Last updated" label, transient feedback) and
    the threaded refresh-with-guard pattern are common to every section, so
    they live here; subclasses only provide a body and a `_do_refresh()`.
    """

    description = ""

    def __init__(self, children):
        self._refreshing = False

        header_children = []
        if self.description:
            header_children.append(
                ipw.HTML(
                    f"<div style='color:{Theme.COLORS.GRAY};font-size:13px;"
                    f"margin:2px 0 6px 0;'>{html.escape(self.description)}</div>"
                )
            )

        self.refresh_button = ipw.Button(description="Refresh", icon="refresh")
        self.refresh_button.on_click(self.refresh)
        self._last_updated = ipw.HTML()
        self.info = ipw.HTML()
        footer = ipw.HBox([self.refresh_button, self._last_updated, self.info])

        super().__init__(
            children=[*header_children, *children, footer],
        )
        self.layout.padding = "8px 0 0 0"

    def show_success(self, text):
        self.info.value = _state_span("ok", text)

    def show_warning(self, text):
        self.info.value = _state_span("warning", text)

    def show_error(self, text):
        self.info.value = _state_span("error", text)

    def show_plain(self, text):
        self.info.value = html.escape(text)

    def refresh(self, _=None):
        if self._refreshing:
            return
        self._refreshing = True
        self.info.value = "Refreshing... <i class='fa fa-spinner fa-spin'></i>"
        self.refresh_button.disabled = True

        def worker():
            try:
                self._do_refresh()
            except Exception as exc:
                self.show_error(f"Failed to refresh: {exc}")
            else:
                # Silent success: the "Last updated" timestamp below already
                # signals success, so no "refreshed" message is shown.
                self.info.value = ""
            finally:
                # Re-enable the button before touching anything else: if a
                # later step raises, the page must not be left with the
                # button permanently disabled.
                self.refresh_button.disabled = False
                self._refreshing = False
                self._last_updated.value = (
                    f"Last updated: {datetime.now().strftime('%H:%M:%S')}"
                )

        threading.Thread(target=worker, daemon=True).start()

    def _do_refresh(self):
        """Synchronous probe/update; subclasses override."""


class DaemonControlWidget(ControlSectionWidget):
    description = "The daemon runs your AiiDA processes in the background."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])


class StatusOverviewWidget(ControlSectionWidget):
    description = "Health of the services behind AiiDA."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])


class SystemResourcesWidget(ControlSectionWidget):
    description = "Memory, CPU and disk usage of this container."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])


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


class DangerZoneWidget(ControlSectionWidget):
    description = "Irreversible actions that can lead to data loss."

    def __init__(self):
        super().__init__([ipw.HTML("To be implemented.")])
