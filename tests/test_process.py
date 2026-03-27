import sys
import types

import pytest
from aiida import orm

from home import process as home_process

pytestmark = pytest.mark.usefixtures("aiida_profile_clean")


@pytest.fixture
def mock_viewer_module(monkeypatch):
    """Provide a lightweight viewer module for optional AWB integration paths."""
    module = types.ModuleType("aiidalab_widgets_base")
    module.viewer = lambda _: "mock-viewer"
    monkeypatch.setitem(sys.modules, "aiidalab_widgets_base", module)


def test_process_inputs_widget(generate_calc_job_node, mock_viewer_module):
    process = generate_calc_job_node(
        inputs={
            "parameters": orm.Int(1),
            "nested": {
                "inner": orm.Int(2),
            },
        }
    )

    home_process.ProcessInputsWidget()

    widget = home_process.ProcessInputsWidget(process=process)
    options = dict(widget._inputs.options)
    assert "parameters" in options
    assert "nested.inner" in options

    nested_uuid = options["nested.inner"]
    widget._inputs.value = nested_uuid
    selected_input = orm.load_node(nested_uuid)
    assert widget.info.value == f"PK: {selected_input.pk}"


def test_process_outputs_widget(multiply_add_completed_workchain, mock_viewer_module):
    home_process.ProcessOutputsWidget()

    widget = home_process.ProcessOutputsWidget(process=multiply_add_completed_workchain)
    widget.show_selected_output(change={"new": "result"})

    selected_output = multiply_add_completed_workchain.outputs["result"]
    assert widget.info.value == f"PK: {selected_output.pk}"


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
    assert (
        f"home/process.ipynb?id={multiply_add_completed_workchain.pk}"
        in widget.table.value
    )


def test_process_list_widget_filters_descriptions(generate_calc_job_node):
    matching_process = generate_calc_job_node(inputs={"parameters": orm.Int(1)})
    matching_process.description = "calc-42 complete"

    other_process = generate_calc_job_node(inputs={"parameters": orm.Int(2)})
    other_process.description = "skip me"

    process_without_description = generate_calc_job_node(
        inputs={"parameters": orm.Int(3)}
    )

    widget = home_process.ProcessListWidget()
    widget.description_contains = r"calc-\d+"
    widget.update()

    assert widget.output.value == "1 processes shown"
    assert "calc-42 complete" in widget.table.value
    assert "skip me" not in widget.table.value
    assert f"home/process.ipynb?id={matching_process.pk}" in widget.table.value
    assert f"home/process.ipynb?id={other_process.pk}" not in widget.table.value
    assert (
        f"home/process.ipynb?id={process_without_description.pk}"
        not in widget.table.value
    )


def test_process_list_widget_renders_empty_results(multiply_add_completed_workchain):
    widget = home_process.ProcessListWidget()
    widget.process_label = "definitely-no-such-process-label"
    widget.update()

    assert widget.output.value == "0 processes shown"
    assert "<table" in widget.table.value
    assert "<th>PK</th>" in widget.table.value
    assert (
        f"home/process.ipynb?id={multiply_add_completed_workchain.pk}"
        not in widget.table.value
    )
