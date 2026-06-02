"""Helpers for optional node previews."""

from __future__ import annotations

import ipywidgets as ipw
from aiida import orm

AWB_UNAVAILABLE_MESSAGE = (
    "The AiiDAlab widgets are not available. Please install it with "
    "`pip install aiidalab-widgets-base` to use this feature."
)


def render_node_preview(node: orm.Node) -> object:
    """Render a node preview using AWB when it is available."""
    try:
        from aiidalab_widgets_base import viewer  # noqa: PLC0415
    except ImportError:
        return ipw.HTML(
            value=str(node) + "<br><em>" + AWB_UNAVAILABLE_MESSAGE + "</em>"
        )
    return viewer(node)
