"""The package reset that the AiiDAlab container performs on its next start.

Kept free of heavy imports: the home page checks it before rendering anything.
"""

import html
import os
from pathlib import Path

import ipywidgets as ipw

from home.themes import ThemeDefault as Theme

# The container's entrypoint (aiidalab-docker-stack,
# before-notebook.d/50_factory-reset.sh) reads this file on start, performs
# the reset mode it contains and removes it. A module constant so tests can
# monkeypatch it: the real file resets ~/.local on the next restart.
_FACTORY_RESET_FILE = Path.home() / "AIIDALAB_FACTORY_RESET"

# The only reset mode of that script; it ignores any other content.
_RESET_MODE = "local"
RESET_LABEL = "Reset user-installed Python packages"


def is_scheduled() -> bool:
    return _FACTORY_RESET_FILE.exists()


def schedule():
    _FACTORY_RESET_FILE.write_text(_RESET_MODE)
    # Read it back: a reset the container won't see is worse than an
    # error, e.g. on a home directory mounted with surprising semantics.
    if _FACTORY_RESET_FILE.read_text() != _RESET_MODE:
        raise RuntimeError("could not verify the content of the flag file")


def cancel():
    _FACTORY_RESET_FILE.unlink(missing_ok=True)


def scheduled_message() -> str:
    """An alert describing the scheduled reset, from what is on disk."""
    try:
        # Like the container, which removes all whitespace.
        mode = "".join(_FACTORY_RESET_FILE.read_text().split())
    except OSError as exc:
        what = f"the flag file could not be read: {exc}"
    else:
        what = (
            RESET_LABEL
            if mode == _RESET_MODE
            else f"unknown mode '{mode}', which the container ignores"
        )
    # Set by JupyterHub in the environment of the user's server.
    hub_home = os.environ.get("JUPYTERHUB_BASE_URL", "/") + "hub/home"
    return f"""
        <div class="alert alert-warning" role="alert">
          <div style="line-height: 1.5">
            A reset has been scheduled: <b>{html.escape(what)}</b>
            <br />
            Please restart your AiiDAlab container.
          </div>
          <div>
            <h2>How to restart</h2>
            <h3>On an AiiDAlab server</h3>
            <ul style="margin: 0; padding-left: 20px; line-height: 1.8">
              <li>
                Open the
                <a class="alert-link" href="{html.escape(hub_home)}" target="_blank">
                  Control Panel</a
                >.
              </li>
              <li>Click <b>Stop My Server</b> and wait.</li>
              <li>Once available, click <b>Start My Server</b>.</li>
            </ul>
            <h3>On a local machine</h3>
            <h4>Using aiidalab-launch</h4>
            <ul style="margin: 0; padding-left: 20px; line-height: 1.8">
              <li>
                Find your active profile with
                <code>aiidalab-launch status</code>
              </li>
              <li>
                Run <code>aiidalab-launch start --restart -p &lt;profile&gt;</code>
              </li>
            </ul>
            <h4>Using Docker directly</h4>
            <ul style="margin: 0; padding-left: 20px; line-height: 1.8">
              <li>Find your active container with <code>docker ps</code></li>
              <li>Run <code>docker restart &lt;container&gt;</code></li>
            </ul>
          </div>
        </div>
    """


class PackageResetNotice(ipw.VBox):
    """Notice of a scheduled package reset, with a way to cancel it."""

    def __init__(self, on_cancelled):
        self._on_cancelled = on_cancelled
        self.cancel_button = ipw.Button(
            description="Cancel package reset",
            icon="undo",
            layout=ipw.Layout(width="fit-content"),
        )
        self.cancel_button.on_click(self._on_cancel)
        self.error = ipw.HTML()
        super().__init__(
            children=[
                ipw.HTML(scheduled_message()),
                self.cancel_button,
                self.error,
            ]
        )

    def _on_cancel(self, _=None):
        try:
            cancel()
        except Exception as exc:
            self.error.value = (
                f"<span style='color: {Theme.COLORS.DANGER}'>"
                f"Failed to cancel the package reset: {html.escape(str(exc))}</span>"
            )
        else:
            self._on_cancelled()
