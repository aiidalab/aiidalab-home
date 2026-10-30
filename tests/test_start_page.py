import ipywidgets as ipw
import pytest

from home import start_page
from home.package_migration import MigrationWidget
from home.package_reset import PackageResetNotice
from home.start_page import AiidaLabHome


@pytest.fixture
def home(monkeypatch):
    """An AiidaLabHome whose apps are a placeholder widget."""
    monkeypatch.setattr(
        AiidaLabHome, "_create_app_children", lambda self: [ipw.HTML("apps")]
    )
    return AiidaLabHome()


@pytest.fixture
def migration_home(migration_paths, monkeypatch, tmp_path):
    monkeypatch.setattr(start_page.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(
        AiidaLabHome, "_create_home_widget", lambda self: ipw.HTML("home")
    )
    monkeypatch.setattr(AiidaLabHome, "load_apps", lambda self: [])
    return AiidaLabHome(), *migration_paths


def test_home_renders_migration_widget_when_pending(migration_home):
    home, marker, flag = migration_home
    marker.mkdir(parents=True)

    children = home._create_app_children()

    assert any(isinstance(child, MigrationWidget) for child in children)
    assert flag.exists()


def test_home_omits_migration_widget_when_not_pending(migration_home):
    home, _, _ = migration_home

    children = home._create_app_children()

    assert not any(isinstance(child, MigrationWidget) for child in children)


def test_home_shows_apps_without_reset(home, reset_flag_file):
    assert [child.value for child in home.render().children] == ["apps"]


def test_home_shows_only_reset_notice_while_scheduled(home, reset_flag_file):
    reset_flag_file.write_text("local")
    (notice,) = home.render().children
    assert isinstance(notice, PackageResetNotice)
    assert "Reset user-installed Python packages" in notice.children[0].value

    # Cancelling brings the apps back.
    notice.cancel_button.click()
    assert not reset_flag_file.exists()
    assert [child.value for child in home.output.children] == ["apps"]
