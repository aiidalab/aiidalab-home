"""The Home shortcuts must use the classic UI and preserve Hub prefixes."""

import importlib.util
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

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


@pytest.mark.parametrize("prefix", ["", "/user/test-user"])
@pytest.mark.parametrize("classic_path", ["", "/nbclassic"])
def test_classic_navigation_preserves_server_base_url(
    monkeypatch, prefix, classic_path
):
    monkeypatch.setattr(nbclassic, "nbclassic_path", lambda: classic_path)
    spec = importlib.util.spec_from_file_location(
        "home_shortcuts", Path(__file__).parents[1] / "start.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    widget = module.get_start_widget("../home", "../../..")
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
