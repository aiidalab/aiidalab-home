"""Helpful utilities for the AiiDA lab tools."""

import sys
from os import path
from importlib import import_module

from markdown import markdown
import ipywidgets as ipw
from aiidalab.config import AIIDALAB_APPS


def load_widget(name):
    if path.exists(path.join(AIIDALAB_APPS, name, 'start.py')):
        return load_start_py(name)
    return load_start_md(name)


def load_start_py(name):
    """Load app appearance from a Python file."""
    try:
        mod = import_module('apps.%s.start' % name)
        appbase = "../" + name
        jupbase = "../../.."
        notebase = jupbase + "/notebooks/apps/" + name
        try:
            return mod.get_start_widget(appbase=appbase, jupbase=jupbase, notebase=notebase)
        except TypeError:
            return mod.get_start_widget(appbase=appbase, jupbase=jupbase)
    except Exception:  # pylint: disable=broad-except
        return ipw.HTML("<pre>{}</pre>".format(sys.exc_info()))


def load_start_md(name):
    """Load app appearance from a Markdown file."""
    fname = path.join(AIIDALAB_APPS, name, 'start.md')
    try:

        md_src = open(fname).read()
        md_src = md_src.replace("](./", "](../{}/".format(name))
        html = markdown(md_src)

        # open links in new window/tab
        html = html.replace('<a ', '<a target="_blank" ')

        # downsize headings
        html = html.replace("<h3", "<h4")
        return ipw.HTML(html)

    except Exception as exc:  # pylint: disable=broad-except
        return ipw.HTML("Could not load start.md: {}".format(str(exc)))


def load_logo(app):
    """Return logo object. Give the priority to the local version"""

    # For some reason standard ipw.Image() app does not work properly.
    res = ipw.HTML('<img src="./aiidalab_logo_v4.svg">', layout={'width': '100px', 'height': '100px'})

    # Checking whether the 'logo' key is present in metadata dictionary.
    if 'logo' not in app.metadata:
        res.value = '<img src="./aiidalab_logo_v4.svg">'

    # If 'logo' key is present and the app is installed.
    elif app.is_installed():
        res.value = '<img src="{}">'.format(path.join('..', app.name, app.metadata['logo']))

    # If not installed, getting file from the remote git repository.
    else:
        # Remove .git if present.
        html_link = path.splitext(app.url)[0]

        # We expect it to always be a git repository
        html_link += '/master/' + app.metadata['logo']
        if 'github.com' in html_link:
            html_link = html_link.replace('github.com', 'raw.githubusercontent.com')
            if html_link.endswith('.svg'):
                html_link += '?sanitize=true'
        res.value = '<img src="{}">'.format(html_link)

    return res
