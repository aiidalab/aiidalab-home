"""The package reset that the AiiDAlab container performs on its next start.

Kept free of heavy imports: the home page checks it before rendering anything.
"""

import html
from pathlib import Path

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
        mode = _FACTORY_RESET_FILE.read_text().strip()
    except OSError as exc:
        what = f"the flag file could not be read: {exc}"
    else:
        what = (
            RESET_LABEL
            if mode == _RESET_MODE
            else f"unknown mode '{mode}', which the container ignores"
        )
    return (
        "<div class='alert alert-warning' role='alert'>"
        f"A reset is scheduled: <b>{html.escape(what)}</b><br>"
        "To perform it, restart your AiiDAlab container, i.e. stop your "
        "AiiDAlab server and start it again. Until then, the apps are not "
        "available, but the reset can be cancelled.</div>"
    )
