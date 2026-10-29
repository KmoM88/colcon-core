# Copyright 2022-2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

"""Package augmentation for PEP 621 packages."""

import logging
import re

from colcon_core.package_augmentation \
    import PackageAugmentationExtensionPoint
from colcon_core.package_augmentation.python \
    import create_dependency_descriptor
from colcon_core.package_augmentation.python import extract_dependencies
from colcon_core.plugin_system import satisfies_version
from colcon_core.python_project.spec import load_and_cache_spec
from colcon_core.python_project.spec import SPEC_NAME


def _normalize_group_name(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def _resolve_dependency_group(normalized_groups, group_name, seen=None):
    norm_name = _normalize_group_name(group_name)
    if seen is None:
        seen = set()
    if norm_name in seen:
        return []
    seen = seen | {norm_name}

    resolved = []
    for item in normalized_groups.get(norm_name) or ():
        if isinstance(item, str):
            resolved.append(item)
        elif isinstance(item, dict) and isinstance(
            item.get('include-group'), str
        ):
            resolved.extend(
                _resolve_dependency_group(
                    normalized_groups, item['include-group'], seen
                )
            )
    return resolved


def _extract_pyproject_options(spec):
    options = {}
    project = spec.get('project') or {}
    if project.get('dependencies'):
        options['install_requires'] = list(project['dependencies'])

    optional_deps = project.get('optional-dependencies') or {}
    extras = {
        key: list(values)
        for key, values in optional_deps.items()
        if isinstance(values, list)
    }
    if extras:
        options['extras_require'] = extras

    raw_groups = spec.get('dependency-groups') or {}
    if isinstance(raw_groups, dict) and raw_groups:
        normalized_groups = {
            _normalize_group_name(k): v
            for k, v in raw_groups.items()
            if isinstance(k, str) and isinstance(v, list)
        }
        test_requirements = []
        for test_group in ('test', 'tests', 'testing'):
            for req in _resolve_dependency_group(
                normalized_groups, test_group
            ):
                if req not in test_requirements:
                    test_requirements.append(req)
        if test_requirements:
            options['tests_require'] = test_requirements

    return options


class PEP621PackageAugmentation(PackageAugmentationExtensionPoint):
    """Augment Python packages with static metadata from `pyproject.toml`."""

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            PackageAugmentationExtensionPoint.EXTENSION_POINT_VERSION,
            '^1.0')

        # avoid debug message from asyncio when colcon uses debug log level
        asyncio_logger = logging.getLogger('asyncio')
        asyncio_logger.setLevel(logging.INFO)

    def augment_package(  # noqa: D102
        self, desc, *, additional_argument_names=None
    ):
        if desc.type != 'python.project':
            return

        spec_file = desc.path / SPEC_NAME
        if not spec_file.is_file():
            return

        spec = load_and_cache_spec(desc)
        desc.dependencies.setdefault('build', set())
        desc.dependencies['build'].update(
            create_dependency_descriptor(d)
            for d in (spec.get('build-system') or {}).get('requires') or ())

        options = _extract_pyproject_options(spec)
        extracted = extract_dependencies(options)
        for dep_type in ('run', 'test'):
            desc.dependencies.setdefault(dep_type, set())
            desc.dependencies[dep_type].update(
                extracted.get(dep_type) or set())

        if 'get_python_setup_options' not in desc.metadata:
            def getter(_env):
                return options

            desc.metadata['get_python_setup_options'] = getter

        project = spec.get('project') or {}
        version = project.get('version')
        if version:
            desc.metadata['version'] = version

        maintainers = project.get('maintainers')
        if not maintainers:
            maintainers = project.get('authors')
        if maintainers:
            desc.metadata.setdefault('maintainers', [])
            for entry in maintainers:
                email = entry.get('email')
                if not email:
                    continue
                name = entry.get('name')
                rfc822 = f'{name} <{email}>' if name else email
                if rfc822 not in desc.metadata['maintainers']:
                    desc.metadata['maintainers'].append(rfc822)
