"""Helpful utilities for the AiiDAlab tools."""

import sys
from importlib.machinery import SourceFileLoader
from os import path

import ipywidgets as ipw
from aiidalab.config import AIIDALAB_APPS
from markdown import markdown


def load_widget(name):
    if path.exists(path.join(AIIDALAB_APPS, name, "start.py")):
        return load_start_py(name)
    return load_start_md(name)


def load_start_py(name):
    """Load app appearance from a Python file."""
    try:
        # Loading start.py from the app's folder
        mod = SourceFileLoader(
            "start", path.join(AIIDALAB_APPS, name, "start.py")
        ).load_module()

        appbase = "../" + name
        jupbase = "../../.."
        notebase = jupbase + "/notebooks/apps/" + name
        try:
            return mod.get_start_widget(
                appbase=appbase, jupbase=jupbase, notebase=notebase
            )
        except TypeError:
            return mod.get_start_widget(appbase=appbase, jupbase=jupbase)
    except Exception:  # pylint: disable=broad-except
        return ipw.HTML("<pre>{}</pre>".format(sys.exc_info()))


def load_start_md(name):
    """Load app appearance from a Markdown file."""
    fname = path.join(AIIDALAB_APPS, name, "start.md")
    try:
        md_src = open(fname).read()
        md_src = md_src.replace("](./", "](../{}/".format(name))
        html = markdown(md_src)

        # open links in new window/tab
        html = html.replace("<a ", '<a target="_blank" ')

        # downsize headings
        html = html.replace("<h3", "<h4")
        return ipw.HTML(html)

    except Exception as exc:  # pylint: disable=broad-except
        return ipw.HTML("Could not load start.md: {}".format(str(exc)))


def load_logo(app):
    """Return HTML widget containing the logo."""

    res = ipw.HTML(
        f'<img src="{app.logo}">',
        layout={"width": "100px", "height": "100px"},
    )
    return res
