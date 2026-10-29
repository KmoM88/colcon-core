# Copyright 2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

from pathlib import Path
from unittest.mock import patch

from colcon_core.extension_point import clear_entry_point_cache
from colcon_core.extension_point import get_all_extension_points
from colcon_core.package_descriptor import PackageDescriptor
from colcon_core.python_project.hook_caller import AsyncHookCaller
from colcon_core.python_project.hook_caller_decorator \
    import get_decorated_hook_caller
from colcon_core.python_project.hook_caller_decorator.setuptools \
    import SetuptoolsDecorator
from colcon_core.python_project.hook_caller_decorator.setuptools \
    import SetuptoolsHookCallerDecoratorExtension
from packaging.version import Version
import pytest

from .run_until_complete import run_until_complete


class _RecordingHookCaller:

    def __init__(self, backend_name='setuptools.build_meta', env=None):
        self.backend_name = backend_name
        self.env = {} if env is None else env
        self.calls = []

    async def call_hook(self, hook_name, **kwargs):
        config = kwargs.get('config_settings')
        snapshot = dict(config) if isinstance(config, dict) else config
        temp_exists = []
        if isinstance(snapshot, dict):
            for opts in snapshot.values():
                if isinstance(opts, list):
                    for opt in opts:
                        if '=' in opt:
                            _, candidate = opt.split('=', 1)
                            temp_exists.append(Path(candidate).is_dir())
        self.calls.append((hook_name, snapshot, temp_exists))
        return 'ok'


def test_setuptools_decorator_non_setuptools_skipped():
    ext = SetuptoolsHookCallerDecoratorExtension()
    caller = AsyncHookCaller('other_backend', env={})
    decorated = ext.decorate_hook_caller(hook_caller=caller)
    assert decorated is caller
    assert 'PYTHONWARNINGS' not in caller.env


def test_setuptools_decorator_pythonwarnings_formatting():
    ext = SetuptoolsHookCallerDecoratorExtension()

    caller_empty = _RecordingHookCaller(env={})
    decorated = ext.decorate_hook_caller(hook_caller=caller_empty)
    assert isinstance(decorated, SetuptoolsDecorator)
    assert not caller_empty.env['PYTHONWARNINGS'].startswith(',')
    assert 'setuptools.command.editable_wheel' in (
        caller_empty.env['PYTHONWARNINGS']
    )

    # Calling a second time with pre-populated PYTHONWARNINGS preserves
    # existing filters without duplicating entries.
    existing = 'default::DeprecationWarning'
    caller_existing = _RecordingHookCaller(
        backend_name='setuptools.build_meta:__legacy__',
        env={'PYTHONWARNINGS': existing},
    )
    ext.decorate_hook_caller(hook_caller=caller_existing)
    ext.decorate_hook_caller(hook_caller=caller_existing)
    parts = caller_existing.env['PYTHONWARNINGS'].split(',')
    assert parts[0] == existing
    assert len(parts) == 2


def test_setuptools_decorator_config_settings_and_temp_cleanup():
    ext = SetuptoolsHookCallerDecoratorExtension()
    caller = _RecordingHookCaller()
    decorated = ext.decorate_hook_caller(hook_caller=caller)

    original_settings = {'--build-option': '--quiet'}
    res = run_until_complete(
        decorated.call_hook(
            'build_wheel',
            wheel_directory='/tmp/out',
            config_settings=original_settings,
        )
    )
    assert res == 'ok'
    # Caller dictionary must not be mutated
    assert original_settings == {'--build-option': '--quiet'}

    hook_name, snapshot, temp_exists = caller.calls[-1]
    assert hook_name == 'build_wheel'
    assert temp_exists == [True, True]
    for opts in snapshot.values():
        for opt in opts:
            if '=' in opt:
                _, candidate = opt.split('=', 1)
                assert not Path(candidate).exists()

    # get_requires_for_build_wheel and get_requires_for_build_editable
    # always isolate egg_info via --global-option
    for req_hook in (
        'get_requires_for_build_wheel',
        'get_requires_for_build_editable',
    ):
        run_until_complete(
            decorated.call_hook(req_hook, config_settings=None)
        )
        _, req_snapshot, req_temp_exists = caller.calls[-1]
        assert '--global-option' in req_snapshot
        assert 'egg_info' in req_snapshot['--global-option']
        assert req_temp_exists == [True]

    # Legacy setuptools (< 64) fallback for prepare_metadata_for_build_wheel
    with patch(
        'colcon_core.python_project.hook_caller_decorator.setuptools.'
        'ESCAPE_HATCH',
        '--global-option',
    ):
        run_until_complete(
            decorated.call_hook('prepare_metadata_for_build_wheel')
        )
        _, md_snapshot, md_temp_exists = caller.calls[-1]
        assert '--global-option' in md_snapshot
        assert md_temp_exists == [True]

    # Other hooks pass straight through
    run_until_complete(decorated.call_hook('build_editable'))
    _, ed_snapshot, _ = caller.calls[-1]
    assert ed_snapshot is None


def test_setuptools_decorator_entry_point_registered():
    clear_entry_point_cache()
    try:
        all_eps = get_all_extension_points()
        group = 'colcon_core.python_project.hook_caller_decorator'
        assert group in all_eps
        assert 'setuptools' in all_eps[group]
    finally:
        clear_entry_point_cache()


def test_setuptools_decorator_real_backend_no_source_pollution(tmp_path):
    setuptools = pytest.importorskip('setuptools', minversion='64.0.0')
    if Version(setuptools.__version__) < Version('70.1'):
        pytest.importorskip('wheel')

    proj_dir = tmp_path / 'pkg'
    pkg_mod = proj_dir / 'clean_pkg'
    pkg_mod.mkdir(parents=True)
    (pkg_mod / '__init__.py').write_text("VALUE = 'clean'\n", encoding='utf-8')
    (proj_dir / 'pyproject.toml').write_text(
        '[build-system]\n'
        'requires = ["setuptools"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '\n'
        '[project]\n'
        'name = "clean-pkg"\n'
        'version = "0.1.0"\n',
        encoding='utf-8',
    )

    out_dir = tmp_path / 'wheels'
    out_dir.mkdir()
    md_dir = tmp_path / 'metadata'
    md_dir.mkdir()

    desc = PackageDescriptor(proj_dir)
    clear_entry_point_cache()
    try:
        caller = get_decorated_hook_caller(desc)
        assert isinstance(caller, SetuptoolsDecorator)

        run_until_complete(caller.call_hook('get_requires_for_build_wheel'))
        run_until_complete(
            caller.call_hook(
                'prepare_metadata_for_build_wheel',
                metadata_directory=str(md_dir),
            )
        )
        whl = run_until_complete(
            caller.call_hook('build_wheel', wheel_directory=str(out_dir))
        )
        assert (out_dir / whl).is_file()

        hooks = run_until_complete(caller.list_hooks())
        if 'get_requires_for_build_editable' in hooks:
            run_until_complete(
                caller.call_hook('get_requires_for_build_editable')
            )
        if 'build_editable' in hooks:
            edit_dir = tmp_path / 'editable'
            edit_dir.mkdir()
            edit_whl = run_until_complete(
                caller.call_hook(
                    'build_editable', wheel_directory=str(edit_dir)
                )
            )
            assert (edit_dir / edit_whl).is_file()
    finally:
        clear_entry_point_cache()

    assert list(proj_dir.glob('*.egg-info')) == []
    assert not (proj_dir / 'build').exists()
