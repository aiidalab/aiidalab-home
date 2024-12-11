from __future__ import annotations

import ipywidgets as ipw
import pandas as pd
from aiida import orm
from IPython.display import display

CONFIG = {
    "column_widths": {
        "Full Label": "25%",
        "Executable Path": "70%",
        "Hide": "5%",
    },
    "cell_style": {"padding": "2px 5px"},
    "rows_per_page": 10,
}


def fetch_code_data():
    """Fetch AiiDA code instances and format them into a DataFrame."""
    codes = orm.Code.collection.all()
    data = []
    for code in codes:
        computer = code.computer
        data.append(
            {
                "Full Label": f"{code.label}@{computer.label}",
                "Executable Path": code.get_executable(),
                "Hide": code.is_hidden,
            }
        )
    return pd.DataFrame(data)


def create_header():
    """Create the header row of the table."""
    header_row = ipw.HBox(
        children=[
            ipw.HTML(
                value=f"<div class='th'>{col}</div>",
                layout=ipw.Layout(width=CONFIG["column_widths"][col]),
            )
            for col in CONFIG["column_widths"]
        ],
    )
    header_row.add_class("header-tr")
    return header_row


def create_row(row, on_checkbox_change):
    """Create a single row in the table."""
    hide_checkbox = ipw.Checkbox(
        value=row["Hide"],
        indent=False,
        tooltip="Check to hide this code in code selection widgets",
        layout=ipw.Layout(width="fit-content", margin="2px 2px 2px 15px"),
    )
    hide_checkbox.full_label = row["Full Label"]
    hide_checkbox.observe(on_checkbox_change, names="value")
    table_row = ipw.HBox(
        children=[
            ipw.Label(
                value=str(row["Full Label"]),
                layout=ipw.Layout(
                    width=CONFIG["column_widths"]["Full Label"],
                    **CONFIG["cell_style"],
                ),
            ),
            ipw.Label(
                value=str(row["Executable Path"]),
                layout=ipw.Layout(
                    width=CONFIG["column_widths"]["Executable Path"],
                    **CONFIG["cell_style"],
                ),
            ),
            ipw.Box(
                children=[hide_checkbox],
                layout=ipw.Layout(
                    width=CONFIG["column_widths"]["Hide"],
                    justify_content="center",
                    **CONFIG["cell_style"],
                ),
            ),
        ],
        layout=ipw.Layout(transition="background-color 0.3s ease", padding="5px"),
    )
    table_row.add_class("tr")
    return table_row


def render_table(df, page, on_checkbox_change):
    """Render the table for the specified page."""
    start_idx = (page - 1) * CONFIG["rows_per_page"]
    end_idx = start_idx + CONFIG["rows_per_page"]
    page_df = df.iloc[start_idx:end_idx]
    header = create_header()
    rows = [create_row(row, on_checkbox_change) for _, row in page_df.iterrows()]
    return ipw.VBox(
        children=[header, *rows],
        layout=ipw.Layout(width="100%"),
    )


def create_paginated_table(df):
    """Create a paginated table with interactive controls."""

    current_page = ipw.IntText(value=1)

    def on_page_change(button):
        current_page.value = int(button.description)
        render_table_with_filters()

    def on_show_active(_):
        render_table_with_filters()

    def on_show_all_click(_):
        unhide_all_codes()
        render_table_with_filters()

    def on_checkbox_change(change):
        update_code_visibility(change.owner.full_label, change.new)
        if show_active.value:
            render_table_with_filters()

    def on_search_change(_):
        render_table_with_filters()

    def update_code_visibility(full_label, is_hidden):
        """Update the visibility of a `Code` node."""
        try:
            code = orm.load_code(full_label)
            code.is_hidden = is_hidden
            df.loc[df["Full Label"] == full_label, "Hide"] = is_hidden
        except Exception:
            df.drop(df[df["Full Label"] == full_label].index, inplace=True)
            render_table_with_filters()

    def unhide_all_codes():
        for code in orm.Code.collection.all():
            full_label = f"{code.label}@{code.computer.label}"
            update_code_visibility(full_label, False)

    def generate_page_buttons(total_pages):
        def create_page_button(page_num):
            button = ipw.Button(
                description=str(page_num),
                button_style="primary" if page_num == current_page.value else "",
                layout=ipw.Layout(width="40px", height="36px"),
                page_number=page_num,
            )
            button.on_click(on_page_change)
            return button

        return [create_page_button(i) for i in range(1, total_pages + 1)]

    def render_table_with_filters():
        """Render the table with current filters applied."""
        visible_df = df.copy()

        if show_active.value:
            visible_df = visible_df[~visible_df["Hide"]]

        query = search_box.value.strip().lower()
        if query:
            visible_df = visible_df[
                visible_df["Full Label"]
                .astype(str)
                .str.lower()
                .str.contains(query, na=False)
                | visible_df["Executable Path"]
                .astype(str)
                .str.lower()
                .str.contains(query, na=False)
            ]

        visible_df.reset_index(drop=True, inplace=True)

        total_pages = (len(visible_df) + CONFIG["rows_per_page"] - 1) // CONFIG[
            "rows_per_page"
        ]
        current_page.max = max(total_pages, 1)
        page = min(current_page.value, total_pages)
        current_page.value = page

        with table_output:
            table_output.clear_output(wait=True)
            display(render_table(visible_df, page, on_checkbox_change))

        pagination.children = generate_page_buttons(total_pages)

    table_output = ipw.Output()

    show_active = ipw.Checkbox(
        value=False,
        description="Show active codes only",
    )
    show_active.observe(
        on_show_active,
        "value",
    )

    show_all_button = ipw.Button(
        description="Show All",
        button_style="primary",
        layout=ipw.Layout(margin="0 0 0 auto"),
    )
    show_all_button.on_click(on_show_all_click)

    search_box = ipw.Text(
        description="Search:",
        placeholder="Filter by label or path...",
        layout=ipw.Layout(width="50%"),
    )
    search_box.observe(
        on_search_change,
        "value",
    )

    filters = ipw.HBox(
        children=[
            search_box,
            show_active,
            show_all_button,
        ],
    )

    pagination = ipw.HBox(layout=ipw.Layout(justify_content="center"))

    render_table_with_filters()

    return ipw.VBox(
        children=[
            filters,
            table_output,
            pagination,
        ],
    )
