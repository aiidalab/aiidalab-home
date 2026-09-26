from home.start_page import needs_migration_warning


def test_needs_migration_warning_for_legacy_app_on_python_312(tmp_path):
    (tmp_path / "aiidalab-widgets-base").mkdir()

    assert needs_migration_warning(tmp_path, (3, 12))


def test_needs_migration_warning_ignores_fresh_python_312_environment(tmp_path):
    assert not needs_migration_warning(tmp_path, (3, 12))


def test_needs_migration_warning_ignores_legacy_python_environment(tmp_path):
    (tmp_path / "aiidalab-widgets-base").mkdir()

    assert not needs_migration_warning(tmp_path, (3, 9))
