import inspect

import pytest
from aiida import orm

from home import node_preview
from home import process as home_process

pytestmark = pytest.mark.usefixtures("aiida_profile_clean")


@pytest.fixture
def capture_display(monkeypatch):
    displayed = []
    monkeypatch.setattr(home_process, "display", displayed.append)
    return displayed


def test_render_node_preview_uses_awb_when_available(monkeypatch):
    node = orm.Int(1)

    monkeypatch.setattr(
        node_preview, "_load_awb_viewer", lambda: lambda _: "mock-viewer"
    )

    assert node_preview.render_node_preview(node) == "mock-viewer"


def test_render_node_preview_returns_message_when_awb_is_unavailable(monkeypatch):
    def raise_import_error():
        raise ImportError

    monkeypatch.setattr(node_preview, "_load_awb_viewer", raise_import_error)

    assert (
        node_preview.render_node_preview(orm.Int(1))
        == node_preview.render_unavailable_preview()
    )


def test_process_inputs_widget_uses_node_preview_adapter(
    generate_calc_job_node, monkeypatch, capture_display
):
    process = generate_calc_job_node(
        inputs={
            "parameters": orm.Int(1),
            "nested": {
                "inner": orm.Int(2),
            },
        }
    )

    monkeypatch.setattr(home_process, "render_node_preview", lambda _: "mock-viewer")

    home_process.ProcessInputsWidget()

    widget = home_process.ProcessInputsWidget(process=process)
    options = dict(widget._inputs.options)
    assert "parameters" in options
    assert "nested.inner" in options

    nested_uuid = options["nested.inner"]
    widget._inputs.value = nested_uuid
    selected_input = orm.load_node(nested_uuid)
    assert widget.info.value == f"PK: {selected_input.pk}"
    assert capture_display == ["mock-viewer"]


def test_process_outputs_widget_shows_unavailable_message(
    multiply_add_completed_workchain, monkeypatch, capture_display
):
    monkeypatch.setattr(
        home_process,
        "render_node_preview",
        lambda _: node_preview.render_unavailable_preview(),
    )

    home_process.ProcessOutputsWidget()

    widget = home_process.ProcessOutputsWidget(process=multiply_add_completed_workchain)
    widget.show_selected_output(change={"new": "result"})

    selected_output = multiply_add_completed_workchain.outputs["result"]
    assert widget.info.value == f"PK: {selected_output.pk}"
    assert capture_display == [node_preview.render_unavailable_preview()]


def test_process_module_does_not_import_awb_directly():
    assert "aiidalab_widgets_base" not in inspect.getsource(home_process)


def test_process_report_widget(multiply_add_completed_workchain):
    home_process.ProcessReportWidget()

    widget = home_process.ProcessReportWidget(process=multiply_add_completed_workchain)
    widget.update()
    assert isinstance(widget.value, str)


def test_process_call_stack_widget(multiply_add_completed_workchain):
    home_process.ProcessCallStackWidget()

    widget = home_process.ProcessCallStackWidget(
        process=multiply_add_completed_workchain
    )
    widget.update()
    assert str(multiply_add_completed_workchain.pk) in widget.value


def test_progress_bar_widget(multiply_add_completed_workchain):
    home_process.ProgressBarWidget()

    widget = home_process.ProgressBarWidget(process=multiply_add_completed_workchain)
    widget.update()
    assert (
        widget.state.value
        == multiply_add_completed_workchain.process_state.value.capitalize()
    )


def test_calcjob_output_widget(generate_calc_job_node):
    process = generate_calc_job_node(inputs={"parameters": orm.Int(1)})

    home_process.CalcJobOutputWidget()

    widget = home_process.CalcJobOutputWidget(calculation=process)
    widget.update()
    assert widget.calculation == process


def test_running_calcjob_output_widget(generate_calc_job_node):
    process = generate_calc_job_node(inputs={"parameters": orm.Int(1)})

    widget = home_process.RunningCalcJobOutputWidget()
    widget.process = process
    widget.update()

    assert isinstance(widget.selection.options, tuple)


def test_process_list_widget(multiply_add_completed_workchain):
    widget = home_process.ProcessListWidget()
    widget.update()
    assert "processes shown" in widget.output.value
    assert "<table" in widget.table.value
