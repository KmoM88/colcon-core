# Copyright 2022-2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

import shlex
from tempfile import TemporaryDirectory

from colcon_core.generic_decorator import GenericDecorator
from colcon_core.plugin_system import satisfies_version
from colcon_core.python_project.hook_caller_decorator \
    import HookCallerDecoratorExtensionPoint
from packaging.version import Version

try:
    from setuptools import __version__ as setuptools_version
except ImportError:
    setuptools_version = '0'


if Version(setuptools_version) < Version('64'):
    ESCAPE_HATCH = '--global-option'
else:
    ESCAPE_HATCH = '--build-option'


def _add_python_warnings(env, new_warnings):
    warnings = [w for w in env.get('PYTHONWARNINGS', '').split(',') if w]
    for new_warning in new_warnings:
        if new_warning not in warnings:
            warnings.append(new_warning)
    if warnings:
        env['PYTHONWARNINGS'] = ','.join(warnings)


def _append_config_options(config_settings, key, options):
    existing = config_settings.get(key)
    if existing is None:
        normalized = []
    elif isinstance(existing, str):
        normalized = shlex.split(existing)
    else:
        normalized = list(existing)
    normalized.extend(options)
    config_settings[key] = normalized


class SetuptoolsHookCallerDecoratorExtension(
    HookCallerDecoratorExtensionPoint
):
    """Decorate a hook caller for a setuptools-based backend."""

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            HookCallerDecoratorExtensionPoint.EXTENSION_POINT_VERSION,
            '^1.0')

    def decorate_hook_caller(self, *, hook_caller):  # noqa: D102
        if hook_caller.backend_name not in (
            'setuptools.build_meta',
            'setuptools.build_meta:__legacy__',
        ):
            return hook_caller
        _add_python_warnings(hook_caller.env, (
            'ignore:Editable installation:setuptools.warnings.InformationOnly:'
            'setuptools.command.editable_wheel',
        ))
        return SetuptoolsDecorator(hook_caller)


class _ScratchBuildBase(TemporaryDirectory):

    def __init__(self, config_settings, option_key=ESCAPE_HATCH):
        self._config_settings = config_settings
        self._option_key = option_key
        super().__init__()

    def __enter__(self):
        temp = super().__enter__()
        _append_config_options(self._config_settings, self._option_key, [
            'build',
            f'--build-base={temp}',
        ])
        return temp


class _ScratchEggBase(TemporaryDirectory):

    def __init__(self, config_settings, option_key=ESCAPE_HATCH):
        self._config_settings = config_settings
        self._option_key = option_key
        super().__init__()

    def __enter__(self):
        temp = super().__enter__()
        _append_config_options(self._config_settings, self._option_key, [
            'egg_info',
            f'--egg-base={temp}',
        ])
        return temp


class SetuptoolsDecorator(GenericDecorator):
    """Enhance hooks to the setuptools build backend."""

    async def call_hook(self, hook_name, **kwargs):  # noqa: D102
        if hook_name in (
            'get_requires_for_build_wheel',
            'get_requires_for_build_editable',
        ):
            config_settings = dict(kwargs.pop('config_settings', None) or {})
            with _ScratchEggBase(
                config_settings, option_key='--global-option'
            ):
                return await self._decoree.call_hook(
                    hook_name, config_settings=config_settings, **kwargs)

        if hook_name == 'prepare_metadata_for_build_wheel':
            if ESCAPE_HATCH != '--global-option':
                return await self._decoree.call_hook(hook_name, **kwargs)
            config_settings = dict(kwargs.pop('config_settings', None) or {})
            with _ScratchEggBase(
                config_settings, option_key='--global-option'
            ):
                return await self._decoree.call_hook(
                    hook_name, config_settings=config_settings, **kwargs)

        if hook_name == 'build_wheel':
            config_settings = dict(kwargs.pop('config_settings', None) or {})
            with _ScratchEggBase(config_settings):
                with _ScratchBuildBase(config_settings):
                    return await self._decoree.call_hook(
                        hook_name, config_settings=config_settings, **kwargs)

        return await self._decoree.call_hook(hook_name, **kwargs)
