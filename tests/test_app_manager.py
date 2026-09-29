from subprocess import CalledProcessError
from unittest.mock import Mock

import pytest

from home.app_manager import AppManagerWidget
from home.widgets import LogOutputWidget


@pytest.mark.parametrize(
    "error",
    [
        None,
        RuntimeError("pip failed"),
        CalledProcessError(1, "post_install"),
    ],
    ids=["success", "pip-failure", "post-install-failure"],
)
def test_reinstall_callback(error):
    log = LogOutputWidget()
    log.value = "previous operation output\n"

    def reinstall(*, stdout):
        assert stdout is log
        assert stdout.value == ""
        stdout.write("installation output\n")
        if error is not None:
            raise error

    app = Mock()
    app.reinstall_app.side_effect = reinstall
    manager = AppManagerWidget.__new__(AppManagerWidget)
    manager.app = app
    manager.dependencies_log = log
    manager._show_msg_success = Mock()
    manager._show_msg_failure = Mock()

    try:
        AppManagerWidget._reinstall_app(manager, None)

        app.reinstall_app.assert_called_once_with(stdout=log)
        if error is None:
            manager._show_msg_success.assert_called_once_with("Reinstalled app.")
            manager._show_msg_failure.assert_not_called()
            assert log.value == ""
        else:
            manager._show_msg_failure.assert_called_once_with(str(error))
            manager._show_msg_success.assert_not_called()
            assert log.value == "installation output\n"
    finally:
        log.close()
