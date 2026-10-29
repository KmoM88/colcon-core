# Copyright 2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

"""Tests for PythonProjectTestTask."""

from argparse import ArgumentParser
from types import SimpleNamespace
from unittest.mock import patch

from colcon_core.extension_point import clear_entry_point_cache
from colcon_core.extension_point import get_all_extension_points
from colcon_core.package_descriptor import PackageDescriptor
from colcon_core.task import TaskContext
from colcon_core.task.python.test import PythonTestingStepExtensionPoint
from colcon_core.task.python.test import PythonTestTask
from colcon_core.task.python_project.test import PythonProjectTestTask

from .extension_point_context import ExtensionPointContext
from .run_until_complete import run_until_complete


class _MockTestingStep(PythonTestingStepExtensionPoint):

    called = False

    def match(self, context, env, setup_py_data):
        return 'pytest' in (setup_py_data.get('tests_require') or ())

    async def step(self, context, env, setup_py_data):
        _MockTestingStep.called = True
        return 0


def test_python_project_test_task_entry_point_and_arguments():
    clear_entry_point_cache()
    try:
        all_eps = get_all_extension_points()
        assert 'python.project' in all_eps['colcon_core.task.test']
    finally:
        clear_entry_point_cache()

    # Registering arguments from both PythonTestTask and PythonProjectTestTask
    # on the same parser must not raise duplicate option errors.
    parser = ArgumentParser()
    PythonTestTask().add_arguments(parser=parser)
    PythonProjectTestTask().add_arguments(parser=parser)


def test_python_project_test_task_dispatches_step(tmp_path):
    _MockTestingStep.called = False
    desc = PackageDescriptor(tmp_path)
    desc.name = 'sample_pkg'
    desc.type = 'python.project'
    desc.metadata['get_python_setup_options'] = (
        lambda _env: {'tests_require': ['pytest']}
    )

    install_base = tmp_path / 'install'
    install_base.mkdir()
    build_base = tmp_path / 'build'
    build_base.mkdir()

    task = PythonProjectTestTask()
    context = TaskContext(
        pkg=desc,
        args=SimpleNamespace(
            path=str(tmp_path),
            build_base=str(build_base),
            install_base=str(install_base),
            test_result_base=None,
            python_testing=None,
        ),
        dependencies={},
    )
    task.set_context(context=context)

    async def _fake_env(_pkg_name, _base, _deps):
        return {'PATH': ''}

    with ExtensionPointContext(mock_step=_MockTestingStep), patch(
        'colcon_core.task.python.test.get_command_environment',
        side_effect=_fake_env,
    ):
        rc = run_until_complete(task.test())

    assert rc == 0
    assert _MockTestingStep.called is True
