from pathlib import Path

import pytest

from home import package_reset


def test_schedule_writes_local_mode(reset_flag_file):
    assert not package_reset.is_scheduled()
    package_reset.schedule()
    assert reset_flag_file.read_text() == "local"
    assert package_reset.is_scheduled()


def test_schedule_raises_when_write_cannot_be_verified(reset_flag_file, monkeypatch):
    original_read_text = Path.read_text

    def _read_text(self, *args, **kwargs):
        if self == reset_flag_file:
            return "garbled"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text)
    with pytest.raises(RuntimeError, match="could not verify"):
        package_reset.schedule()


def test_cancel_removes_flag_file(reset_flag_file):
    reset_flag_file.write_text("local")
    package_reset.cancel()
    assert not package_reset.is_scheduled()
    # Cancelling a reset that is not scheduled is not an error.
    package_reset.cancel()


@pytest.mark.parametrize("content", ["local\n", " lo cal \n"])
def test_scheduled_message_for_local_mode(reset_flag_file, content):
    # The container removes all whitespace, so these are still `local`.
    reset_flag_file.write_text(content)
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


def test_notice_cancel(reset_flag_file):
    reset_flag_file.write_text("local")
    cancelled = []
    notice = package_reset.PackageResetNotice(on_cancelled=lambda: cancelled.append(1))
    notice.cancel_button.click()
    assert not reset_flag_file.exists()
    # Cancelling a reset that is already gone is not an error.
    notice.cancel_button.click()
    assert notice.error.value == ""
    assert cancelled == [1, 1]


def test_notice_cancel_failure(reset_flag_file):
    # A directory in place of the flag file exists, but can't be removed.
    reset_flag_file.mkdir()
    cancelled = []
    notice = package_reset.PackageResetNotice(on_cancelled=lambda: cancelled.append(1))
    notice.cancel_button.click()
    assert "Failed to cancel" in notice.error.value
    assert cancelled == []
