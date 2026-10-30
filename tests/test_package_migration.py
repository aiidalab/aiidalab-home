import html

from home.package_migration import MigrationWidget


def test_migration_marker_creates_persistent_flag(migration_paths):
    marker, flag = migration_paths
    marker.mkdir(parents=True)

    widget = MigrationWidget()

    assert flag.exists()
    assert widget.confirm_button.disabled


def test_migration_is_pending_when_marker_or_flag_exists(migration_paths):
    marker, flag = migration_paths

    assert not MigrationWidget.has_pending_migration()
    marker.mkdir(parents=True)
    assert MigrationWidget.has_pending_migration()
    marker.rmdir()
    flag.touch()
    assert MigrationWidget.has_pending_migration()


def test_migration_confirmation_requires_all_steps(migration_paths):
    _, flag = migration_paths
    flag.touch()
    widget = MigrationWidget()

    for step in widget.steps[:-1]:
        step.value = True

    assert widget.confirm_button.disabled
    assert flag.exists()


def test_migration_confirmation_is_refused_until_marker_is_removed(migration_paths):
    marker, flag = migration_paths
    marker.mkdir(parents=True)
    widget = MigrationWidget()
    for step in widget.steps:
        step.value = True

    widget.confirm_button.click()

    assert flag.exists()
    assert "still exists" in widget.status.value
    assert widget.layout.display != "none"

    marker.rmdir()
    widget.confirm_button.click()

    assert not flag.exists()
    assert widget.layout.display == "none"


def test_failed_flag_write_shows_error(migration_paths, monkeypatch):
    marker, _ = migration_paths
    marker.mkdir(parents=True)
    # touch() fails: the parent directory does not exist.
    flag = marker.parent / "missing" / ".FLAG_PENDING_MIGRATION"
    monkeypatch.setattr(MigrationWidget, "_PENDING_FLAG", flag)

    widget = MigrationWidget()

    message = html.unescape(widget.status.value)
    assert "Could not save the migration reminder" in message
    assert f"'{flag}'" in message


def test_failed_flag_removal_shows_error_and_keeps_widget(migration_paths):
    _, flag = migration_paths
    # unlink() fails: the flag is a directory.
    flag.mkdir()
    widget = MigrationWidget()
    for step in widget.steps:
        step.value = True

    widget.confirm_button.click()

    message = html.unescape(widget.status.value)
    assert "Could not clear the migration reminder" in message
    assert f"'{flag}'" in message
    assert widget.layout.display != "none"
