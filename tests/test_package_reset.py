import pytest

from home import package_reset


def test_schedule_writes_local_mode(reset_flag_file):
    assert not package_reset.is_scheduled()
    package_reset.schedule()
    assert reset_flag_file.read_text() == "local"
    assert package_reset.is_scheduled()


def test_cancel_removes_flag_file(reset_flag_file):
    reset_flag_file.write_text("local")
    package_reset.cancel()
    assert not package_reset.is_scheduled()
    # Cancelling a reset that is not scheduled is not an error.
    package_reset.cancel()


def test_scheduled_message_for_local_mode(reset_flag_file):
    # The container strips whitespace, so a trailing newline is still `local`.
    reset_flag_file.write_text("local\n")
    assert "Reset user-installed Python packages" in package_reset.scheduled_message()


@pytest.mark.parametrize("value", ["1", "2", "", "<b>x</b>"])
def test_scheduled_message_for_unknown_mode(reset_flag_file, value):
    reset_flag_file.write_text(value)
    message = package_reset.scheduled_message()
    assert "which the container ignores" in message
    # The file content is escaped, not rendered.
    assert "<b>x</b>" not in message


def test_scheduled_message_for_unreadable_flag_file(reset_flag_file):
    reset_flag_file.mkdir()
    assert "could not be read" in package_reset.scheduled_message()
