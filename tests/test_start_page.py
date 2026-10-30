import ipywidgets as ipw
import pytest

from home.package_reset import PackageResetNotice
from home.start_page import AiidaLabHome


@pytest.fixture
def home(monkeypatch):
    """An AiidaLabHome whose apps are a placeholder widget."""
    monkeypatch.setattr(
        AiidaLabHome, "_create_app_children", lambda self: [ipw.HTML("apps")]
    )
    return AiidaLabHome()


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
