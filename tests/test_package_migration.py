import pytest

from home.package_migration import MigrationWidget


@pytest.fixture
def migration_paths(tmp_path, monkeypatch):
    marker = tmp_path / ".local" / "lib" / "python3.9"
    flag = tmp_path / ".FLAG_PENDING_MIGRATION"
    monkeypatch.setattr(MigrationWidget, "_LEGACY_USER_SITE", marker)
    monkeypatch.setattr(MigrationWidget, "_PENDING_FLAG", flag)
    return marker, flag


def test_migration_marker_creates_persistent_flag(migration_paths):
    marker, flag = migration_paths
    marker.mkdir(parents=True)

    widget = MigrationWidget()

    assert MigrationWidget.has_pending_migration()
    assert flag.exists()
    assert widget.confirm_button.disabled
    assert [step.description for step in widget.steps] == [
        "Reset local software",
        "Removed AWB if installed",
        "Reinstalled apps",
    ]
    banner_html = widget.children[0].value
    assert "⚠️ LEGACY ENVIRONMENT DETECTED ⚠️" in banner_html
    assert "read all steps carefully before proceeding" in banner_html


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


def test_migration_confirmation_keeps_flag_while_marker_exists(migration_paths):
    marker, flag = migration_paths
    marker.mkdir(parents=True)
    widget = MigrationWidget()
    for step in widget.steps:
        step.value = True

    widget.confirm_button.click()

    assert flag.exists()
    assert "still exists" in widget.status.value


def test_migration_confirmation_clears_flag_after_marker_is_removed(migration_paths):
    marker, flag = migration_paths
    marker.mkdir(parents=True)
    widget = MigrationWidget()
    for step in widget.steps:
        step.value = True
    marker.rmdir()

    widget.confirm_button.click()

    assert not flag.exists()
    assert widget.layout.display == "none"
