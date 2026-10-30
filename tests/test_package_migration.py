from home.package_migration import MigrationWidget, has_pending_migration


def test_migration_marker_creates_persistent_flag(tmp_path):
    marker = tmp_path / ".local" / "lib" / "python3.9"
    marker.mkdir(parents=True)
    flag = tmp_path / ".FLAG_PENDING_MIGRATION"

    widget = MigrationWidget(marker, flag)

    assert widget.visible
    assert flag.exists()
    assert widget.confirm_button.disabled
    assert [step.description for step in widget.steps] == [
        "Reset local software",
        "Removed AWB if installed",
        "Reinstalled apps",
    ]
    banner_html = widget.children[0].value
    assert "⚠️ LEGACY ENVIRONMENT DETECTED ⚠️" in banner_html


def test_migration_is_pending_when_marker_or_flag_exists(tmp_path):
    marker = tmp_path / ".local" / "lib" / "python3.9"
    flag = tmp_path / ".FLAG_PENDING_MIGRATION"

    assert not has_pending_migration(marker, flag)
    marker.mkdir(parents=True)
    assert has_pending_migration(marker, flag)
    marker.rmdir()
    flag.touch()
    assert has_pending_migration(marker, flag)


def test_migration_confirmation_requires_all_steps(tmp_path):
    flag = tmp_path / ".FLAG_PENDING_MIGRATION"
    flag.touch()
    widget = MigrationWidget(tmp_path / "missing-marker", flag)

    for step in widget.steps[:-1]:
        step.value = True

    assert widget.confirm_button.disabled
    assert flag.exists()


def test_migration_confirmation_keeps_flag_while_marker_exists(tmp_path):
    marker = tmp_path / ".local" / "lib" / "python3.9"
    marker.mkdir(parents=True)
    flag = tmp_path / ".FLAG_PENDING_MIGRATION"
    widget = MigrationWidget(marker, flag)
    for step in widget.steps:
        step.value = True

    widget.confirm_button.click()

    assert flag.exists()
    assert "still exists" in widget.status.value


def test_migration_confirmation_clears_flag_after_marker_is_removed(tmp_path):
    marker = tmp_path / ".local" / "lib" / "python3.9"
    marker.mkdir(parents=True)
    flag = tmp_path / ".FLAG_PENDING_MIGRATION"
    widget = MigrationWidget(marker, flag)
    for step in widget.steps:
        step.value = True
    marker.rmdir()

    widget.confirm_button.click()

    assert not flag.exists()
    assert widget.layout.display == "none"
