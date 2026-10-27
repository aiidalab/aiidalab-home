import pytest

from home import package_reset


def test_schedule_writes_local_mode(reset_flag_file, tmp_path):
    # The container ignores a symlink; scheduling replaces it.
    target = tmp_path / "target"
    target.write_text("local")
    reset_flag_file.symlink_to(target)
    assert not package_reset.is_scheduled()
    package_reset.schedule()
    assert not reset_flag_file.is_symlink()
    assert package_reset.is_scheduled()


def test_cancel_removes_flag_file(reset_flag_file):
    reset_flag_file.write_text("local")
    package_reset.cancel()
    assert not package_reset.is_scheduled()
    # Cancelling a reset that is not scheduled is not an error.
    package_reset.cancel()


@pytest.mark.parametrize(
    ("content", "scheduled"),
    # Like the container: whitespace removed, anything but `local` ignored.
    [
        (b"local", True),
        (b" lo cal \n", True),
        (b"", False),
        (b"1", False),
        (b"\xff", False),
    ],
)
def test_is_scheduled(reset_flag_file, content, scheduled):
    reset_flag_file.write_bytes(content)
    assert package_reset.is_scheduled() is scheduled


def test_is_scheduled_ignores_a_directory(reset_flag_file):
    reset_flag_file.mkdir()
    assert not package_reset.is_scheduled()


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
