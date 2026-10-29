# Copyright 2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

"""Tests for colcon_core.python_project.metadata."""

from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from colcon_core.package_descriptor import PackageDescriptor
from colcon_core.python_project.metadata import load_and_cache_metadata
from colcon_core.python_project.metadata import load_metadata
from colcon_core.python_project.metadata import options_from_metadata
import pytest

from .run_until_complete import run_until_complete


class _PrepareMetadataCaller:

    async def list_hooks(self):
        return ['build_wheel', 'prepare_metadata_for_build_wheel']

    async def call_hook(self, hook_name, **kwargs):
        assert hook_name == 'prepare_metadata_for_build_wheel'
        dist_info = Path(kwargs['metadata_directory']) / 'demo-1.2.3.dist-info'
        dist_info.mkdir(parents=True)
        (dist_info / 'METADATA').write_text(
            'Metadata-Version: 2.1\n'
            'Name: demo\n'
            'Version: 1.2.3\n'
            'Requires-Dist: requests>=2.0\n'
            "Requires-Dist: pytest>=7.0 ; extra == 'test'\n"
            "Requires-Dist: sphinx ; extra == 'docs'\n",
            encoding='utf-8',
        )
        return dist_info.name


class _WheelFallbackCaller:

    def __init__(self, include_dist_info=True):
        self._include_dist_info = include_dist_info

    async def list_hooks(self):
        return ['build_wheel']

    async def call_hook(self, hook_name, **kwargs):
        assert hook_name == 'build_wheel'
        wheel_path = (
            Path(kwargs['wheel_directory']) / 'demo-2.0.0-py3-none-any.whl'
        )
        with ZipFile(wheel_path, 'w') as wf:
            wf.writestr('demo/__init__.py', '')
            if self._include_dist_info:
                wf.writestr(
                    'demo-2.0.0.dist-info/METADATA',
                    'Metadata-Version: 2.1\nName: demo\nVersion: 2.0.0\n',
                )
        return wheel_path.name


def test_load_and_cache_metadata_prepare_hook(tmp_path):
    desc = PackageDescriptor(tmp_path)
    caller = _PrepareMetadataCaller()
    with patch(
        'colcon_core.python_project.metadata.get_decorated_hook_caller',
        return_value=caller,
    ) as get_caller:
        md1 = run_until_complete(load_and_cache_metadata(desc))
        md2 = run_until_complete(load_and_cache_metadata(desc))

    assert get_caller.call_count == 1
    assert md1 is md2
    assert md1['Version'] == '1.2.3'

    options = desc.metadata['get_python_setup_options']({})
    assert options == {
        'install_requires': ['requests >= 2.0'],
        'extras_require': {'test': ['pytest >= 7.0']},
    }
    assert options_from_metadata(md1) == options


def test_load_metadata_build_wheel_fallback(tmp_path):
    desc = PackageDescriptor(tmp_path)
    with patch(
        'colcon_core.python_project.metadata.get_decorated_hook_caller',
        return_value=_WheelFallbackCaller(include_dist_info=True),
    ):
        md = run_until_complete(load_metadata(desc))
    assert md['Version'] == '2.0.0'

    with patch(
        'colcon_core.python_project.metadata.get_decorated_hook_caller',
        return_value=_WheelFallbackCaller(include_dist_info=False),
    ):
        with pytest.raises(RuntimeError):
            run_until_complete(load_metadata(desc))
