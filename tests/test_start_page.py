from home.widgets import MigrationWidget


def test_migration_marker_creates_persistent_flag(tmp_path):
    marker = tmp_path / ".local" / "lib" / "python3.9"
    marker.mkdir(parents=True)
    flag = tmp_path / ".PENDING_MIGRATION_FLAG"

    widget = MigrationWidget(marker, flag)

    assert widget.visible
    assert flag.exists()
    assert widget.confirm_button.disabled
    assert [step.description for step in widget.steps] == [
        "Reset local software and restarted container",
        "Removed AWB if installed",
        "Reinstalled apps",
        "Restarted daemon",
    ]
    banner_html = widget.children[0].value
    assert "⚠️ LEGACY ENVIRONMENT DETECTED ⚠️" in banner_html
    assert "Control &gt; Danger zone" in banner_html
    assert "Control &gt; Daemon" in banner_html
    assert "<ul>" in banner_html
    assert "remove its folder (in ~/apps)" in banner_html
    assert "contact its developers to request a compatible release" in banner_html


def test_existing_migration_flag_shows_widget_without_marker(tmp_path):
    flag = tmp_path / ".PENDING_MIGRATION_FLAG"
    flag.touch()

    widget = MigrationWidget(tmp_path / "missing-marker", flag)

    assert widget.visible
    assert widget.layout.display != "none"


def test_migration_confirmation_requires_all_steps(tmp_path):
    flag = tmp_path / ".PENDING_MIGRATION_FLAG"
    flag.touch()
    widget = MigrationWidget(tmp_path / "missing-marker", flag)

    for step in widget.steps[:-1]:
        step.value = True

    assert widget.confirm_button.disabled
    assert flag.exists()


def test_migration_confirmation_keeps_flag_while_marker_exists(tmp_path):
    marker = tmp_path / ".local" / "lib" / "python3.9"
    marker.mkdir(parents=True)
    flag = tmp_path / ".PENDING_MIGRATION_FLAG"
    widget = MigrationWidget(marker, flag)
    for step in widget.steps:
        step.value = True

    widget.confirm_button.click()

    assert flag.exists()
    assert "still exists" in widget.status.value


def test_migration_confirmation_clears_flag_after_marker_is_removed(tmp_path):
    marker = tmp_path / ".local" / "lib" / "python3.9"
    marker.mkdir(parents=True)
    flag = tmp_path / ".PENDING_MIGRATION_FLAG"
    widget = MigrationWidget(marker, flag)
    for step in widget.steps:
        step.value = True
    marker.rmdir()

    widget.confirm_button.click()

    assert not flag.exists()
    assert widget.layout.display == "none"
