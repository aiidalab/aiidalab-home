"""Helpers for optional node previews."""

from __future__ import annotations

from aiida import orm

AWB_UNAVAILABLE_MESSAGE = (
    "The AiiDAlab widgets are not available. Please install it with "
    "`pip install aiidalab-widgets-base` to use this feature."
)


def _load_awb_viewer():
    from aiidalab_widgets_base import viewer  # noqa: PLC0415

    return viewer


def render_unavailable_preview() -> str:
    """Return the fallback message shown when AWB is unavailable."""
    return AWB_UNAVAILABLE_MESSAGE


def render_node_preview(node: orm.Node) -> object:
    """Render a node preview using AWB when it is available."""
    try:
        viewer = _load_awb_viewer()
    except ImportError:
        return render_unavailable_preview()

    return viewer(node)
