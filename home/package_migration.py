"""The package migration reminder shown on the AiiDAlab home page."""

import html
from pathlib import Path

import ipywidgets as ipw


class MigrationWidget(ipw.VBox):
    """Guide users through the Python 3.9 to 3.12 environment migration."""

    # Packages the user pip-installed under the Python 3.9 image.
    _LEGACY_USER_SITE = Path.home() / ".local" / "lib" / "python3.9"

    # Keeps the notice up after the reset has removed the directory above,
    # until the user confirms that the apps were reinstalled.
    _PENDING_FLAG = Path.home() / ".FLAG_PENDING_MIGRATION"

    _INSTRUCTIONS = """
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
    """

    def __init__(self):
        self.status = ipw.HTML()

        if self._LEGACY_USER_SITE.exists():
            self._mark_pending_migration()

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
                ipw.HTML(self._INSTRUCTIONS),
                *self.steps,
                self.confirm_button,
                self.status,
            ]
        )
        self.add_class("migration-banner")

    @classmethod
    def has_pending_migration(cls):
        """Check if there is a pending migration."""
        return cls._LEGACY_USER_SITE.exists() or cls._PENDING_FLAG.exists()

    def _mark_pending_migration(self):
        """Save the migration reminder as _PENDING_FLAG."""
        try:
            self._PENDING_FLAG.touch()
        except OSError as exc:
            self._show_error(f"Could not save the migration reminder: {exc}")

    def _update_confirmation_state(self, _=None):
        self.confirm_button.disabled = not all(step.value for step in self.steps)

    def _confirm_migration(self, _=None):
        if self._LEGACY_USER_SITE.exists():
            self._show_error(
                "The Python 3.9 user-site directory still exists. The migration reminder has not been cleared."
            )
            return

        try:
            self._PENDING_FLAG.unlink(missing_ok=True)
        except OSError as exc:
            self._show_error(f"Could not clear the migration reminder: {exc}")
            return

        self.layout.display = "none"

    def _show_error(self, message):
        self.status.value = f"""
            <div class='alert alert-danger' style='margin: 0.5rem 0 0' role='alert'>
                {html.escape(message)}
            </div>
        """
