"""The package migration reminder shown on the AiiDAlab home page."""

import html
from pathlib import Path

import ipywidgets as ipw


def _get_migration_paths(marker_path=None, flag_path=None):
    """Return the paths for the legacy marker and persistent migration flag."""
    home = Path.home()
    return (
        marker_path
        if marker_path is not None
        else home / ".local" / "lib" / "python3.9",
        flag_path if flag_path is not None else home / ".FLAG_PENDING_MIGRATION",
    )


def has_pending_migration(marker_path=None, flag_path=None):
    """Return whether a migration is pending."""
    marker_path, flag_path = _get_migration_paths(marker_path, flag_path)
    return marker_path.exists() or flag_path.exists()


class MigrationWidget(ipw.VBox):
    """Guide users through the Python 3.9 to 3.12 environment migration."""

    def __init__(self, marker_path=None, flag_path=None):
        self.marker_path, self.flag_path = _get_migration_paths(marker_path, flag_path)
        self.status = ipw.HTML()

        if self.marker_path.exists() and not self.flag_path.exists():
            try:
                self.flag_path.touch()
            except OSError as exc:
                self.status.value = (
                    f"Could not save the migration reminder: {html.escape(str(exc))}"
                )

        self.visible = has_pending_migration(self.marker_path, self.flag_path)

        instructions = ipw.HTML("""
            <h2 style='text-align: center'>⚠️ LEGACY ENVIRONMENT DETECTED ⚠️</h2>
            <p>The following steps <b>must be completed prior to using the apps</b>. Please <b>read all steps carefully before proceeding</b>:</p>
            <ul>
                <li>From <b>Control</b> &gt; <b>Danger zone</b>, schedule a local software reset.</li>
                <li>Restart the container and return to the home page.</li>
                <li>
                <p>For each app, click <b>Manage App</b>, then:</p>
                <ul>
                    <li>If the app is AiiDAlab widgets base (AWB), uninstall it*.</li>
                    <li>For all other apps, click <b>Update</b> to update the app to a compatible version**.</li>
                    <li>Refresh the home page to confirm the app is now compatible.</li>
                </ul>
                </li>
                <li>Refer any migration issues to the relevant app developers.</li>
            </ul>
            <p><em>*AWB is now pre-installed as a library</em></p>
            <p><em>**If an app has no compatible version, contact its developers to request a compatible release</em></p>
            <div style="border-top: 1px solid #d8a426; margin: 12px 0;"></div>
            <p><b>Once all steps are complete</b>, check each box below and confirm to proceed:</p>
        """)

        self.steps = [
            ipw.Checkbox(
                description="Reset local software",
                indent=False,
            ),
            ipw.Checkbox(
                description="Removed AWB if installed",
                indent=False,
            ),
            ipw.Checkbox(
                description="Reinstalled apps",
                indent=False,
            ),
        ]
        for step in self.steps:
            step.observe(self._update_confirmation_state, names="value")

        self.confirm_button = ipw.Button(
            description="Confirm migration",
            button_style="success",
            layout={"width": "fit-content"},
            icon="check",
            disabled=True,
        )
        self.confirm_button.on_click(self._confirm_migration)

        super().__init__(
            children=[
                instructions,
                *self.steps,
                self.confirm_button,
                self.status,
            ]
        )
        self.add_class("migration-banner")
        if not self.visible:
            self.layout.display = "none"

    def _update_confirmation_state(self, _=None):
        self.confirm_button.disabled = not all(step.value for step in self.steps)

    def _confirm_migration(self, _=None):
        if self.marker_path.exists():
            self.status.value = """
                <div class='alert alert-danger' style='margin: 0.5rem 0 0' role='alert'>
                    The Python 3.9 user-site directory still exists.
                    The migration reminder has not been cleared.
                </div>
            """
            return

        try:
            self.flag_path.unlink(missing_ok=True)
        except OSError as exc:
            self.status.value = (
                f"Could not clear the migration reminder: {html.escape(str(exc))}"
            )
            return

        self.status.value = "Migration confirmed."
        self.layout.display = "none"
