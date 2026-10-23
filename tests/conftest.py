from collections.abc import Mapping
from pathlib import Path

import pytest
from aiida import engine, orm
from aiida.common import LinkType
from aiida.workflows.arithmetic.multiply_add import MultiplyAddWorkChain
from plumpy import ProcessState

import home.package_reset

pytest_plugins = ["aiida.manage.tests.pytest_fixtures"]

# The container resets ~/.local on its next start while this file exists.
_REAL_RESET_FLAG_FILE = Path.home() / "AIIDALAB_FACTORY_RESET"


@pytest.fixture(scope="session", autouse=True)
def _real_reset_flag_file_untouched():
    """Fail the session if a test wrote the real ~/AIIDALAB_FACTORY_RESET."""
    existed = _REAL_RESET_FLAG_FILE.exists()
    yield
    assert existed or not _REAL_RESET_FLAG_FILE.exists(), (
        f"a test created {_REAL_RESET_FLAG_FILE}; remove it"
    )


@pytest.fixture
def reset_flag_file(tmp_path, monkeypatch):
    """A temporary path in place of the package reset's flag file."""
    flag_file = tmp_path / "AIIDALAB_FACTORY_RESET"
    monkeypatch.setattr(home.package_reset, "_FACTORY_RESET_FILE", flag_file)
    return flag_file


@pytest.fixture
def generate_calc_job_node(aiida_localhost):
    """Generate a mock ``CalcJobNode`` with linked inputs."""

    def flatten_inputs(inputs, prefix=""):
        flat_inputs = []
        for key, value in inputs.items():
            if isinstance(value, Mapping):
                flat_inputs.extend(flatten_inputs(value, prefix=prefix + key + "__"))
            else:
                flat_inputs.append((prefix + key, value))
        return flat_inputs

    def _generate_calc_job_node(inputs=None, attributes=None):
        node = orm.CalcJobNode(
            computer=aiida_localhost,
            process_type="aiida.calculations:core.arithmetic.add",
        )
        node.base.attributes.set("input_filename", "aiida.in")
        node.base.attributes.set("output_filename", "aiida.out")
        node.base.attributes.set("error_filename", "aiida.err")
        node.set_option("resources", {"num_machines": 1, "num_mpiprocs_per_machine": 1})
        node.set_option("max_wallclock_seconds", 1800)

        if attributes:
            node.base.attributes.set_many(attributes)

        if inputs:
            for link_label, input_node in flatten_inputs(inputs):
                input_node.store()
                node.base.links.add_incoming(
                    input_node, link_type=LinkType.INPUT_CALC, link_label=link_label
                )

        node.store()

        remote_folder = orm.RemoteData(computer=aiida_localhost, remote_path="/tmp")
        remote_folder.base.links.add_incoming(
            node, link_type=LinkType.CREATE, link_label="remote_folder"
        )
        remote_folder.store()

        node.set_process_state(ProcessState.FINISHED)
        node.set_exit_status(0)
        return node

    return _generate_calc_job_node


@pytest.fixture
def aiida_local_code_bash(aiida_local_code_factory):
    """Return a ``Code`` configured for the bash executable."""
    return aiida_local_code_factory(executable="bash", entry_point="bash")


@pytest.fixture
def multiply_add_completed_workchain(aiida_local_code_bash):
    """Return a ``MultiplyAddWorkChain`` with a finished process state."""

    inputs = {
        "x": orm.Int(1),
        "y": orm.Int(2),
        "z": orm.Int(3),
        "code": aiida_local_code_bash,
    }
    _, process = engine.run_get_node(MultiplyAddWorkChain, **inputs)  # ty: ignore[invalid-argument-type]
    return process
