"""The Home shortcuts must use the classic UI and preserve Hub prefixes."""

import importlib.util
import sys
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin

import ipywidgets as ipw
import nbclassic
import pytest


class Links(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.urls = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "title" in attrs:
            self.urls[attrs["title"]] = attrs["href"]


def load_start_widget():
    spec = importlib.util.spec_from_file_location(
        "home_shortcuts", Path(__file__).parents[1] / "start.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.get_start_widget("../home", "../../..")


@pytest.mark.parametrize("prefix", ["", "/user/test-user"])
@pytest.mark.parametrize("classic_path", ["", "/nbclassic"])
def test_classic_navigation_preserves_server_base_url(
    monkeypatch, prefix, classic_path
):
    monkeypatch.setattr(nbclassic, "nbclassic_path", lambda: classic_path)
    monkeypatch.setitem(sys.modules, "aiidalab_ai_assistant", None)
    widget = load_start_widget()
    try:
        links = Links(widget.value).urls
        page_url = f"http://localhost{prefix}/apps/apps/home/start.ipynb"
        assert urljoin(page_url, links["File Manager"]) == (
            f"http://localhost{prefix}{classic_path}/tree/"
        )
        assert urljoin(page_url, links["Task Manager"]) == (
            f"http://localhost{prefix}{classic_path}/tree/#running"
        )
        assert urljoin(page_url, links["Open Terminal"]) == (
            f"http://localhost{prefix}/apps/apps/home/terminal.ipynb"
        )
    finally:
        widget.close()


def test_home_adds_the_panel_when_the_assistant_is_installed(monkeypatch):
    assistant = ipw.Label("Test assistant panel")

    def panel(*, apps_url):
        assert apps_url == "../"
        return assistant

    monkeypatch.setitem(
        sys.modules, "aiidalab_ai_assistant", SimpleNamespace(AssistantPanel=panel)
    )
    widget = load_start_widget()
    try:
        assert widget.children[0] is assistant
        assert "Open Terminal" in Links(widget.children[1].value).urls
    finally:
        for child in widget.children:
            child.close()
        widget.close()
