# Copyright 2022-2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

"""Package augmentation for PEP 621 packages."""

import asyncio
from email.utils import getaddresses
import logging
from subprocess import CalledProcessError

from colcon_core.package_augmentation import logger
from colcon_core.package_augmentation \
    import PackageAugmentationExtensionPoint
from colcon_core.package_augmentation.python \
    import create_dependency_descriptor
from colcon_core.plugin_system import satisfies_version
from colcon_core.python_project.metadata import load_and_cache_metadata
from colcon_core.python_project.metadata import options_from_metadata
from colcon_core.python_project.spec import load_and_cache_spec
from colcon_core.python_project.spec import SPEC_NAME
from colcon_core.subprocess import new_event_loop

_DYNAMIC_FIELDS = {
    'authors',
    'dependencies',
    'maintainers',
    'optional-dependencies',
    'version',
}


def _load_dynamic_metadata(desc):
    loop = new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(load_and_cache_metadata(desc))
    except (CalledProcessError, OSError, RuntimeError, ValueError) as e:
        logger.warning(
            f"Failed to load dynamic metadata for '{desc.name}': {e}")
        return None
    finally:
        loop.close()


def _augment_maintainers_from_metadata(desc, metadata):
    raw_headers = (
        metadata.get_all('Maintainer-email') or
        metadata.get_all('Author-email') or
        ()
    )
    if not raw_headers:
        return
    desc.metadata.setdefault('maintainers', [])
    for person_name, email_address in getaddresses(raw_headers):
        if not email_address:
            continue
        rfc822 = (
            f'{person_name} <{email_address}>'
            if person_name else email_address
        )
        if rfc822 not in desc.metadata['maintainers']:
            desc.metadata['maintainers'].append(rfc822)


class PEP621PackageAugmentation(PackageAugmentationExtensionPoint):
    """Augment Python packages with metadata from `pyproject.toml`."""

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

        project = spec.get('project', {})
        desc.dependencies.setdefault('run', set())
        desc.dependencies['run'].update(
            create_dependency_descriptor(d)
            for d in project.get('dependencies') or ())

        optional_deps = project.get('optional-dependencies', {})
        desc.dependencies.setdefault('test', set())
        desc.dependencies['test'].update(
            create_dependency_descriptor(d)
            for d in optional_deps.get('test') or ())

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

        dynamic_fields = set(project.get('dynamic') or ()) & _DYNAMIC_FIELDS
        if dynamic_fields:
            metadata = _load_dynamic_metadata(desc)
            if metadata is not None:
                options = options_from_metadata(metadata)
                if 'dependencies' in dynamic_fields:
                    desc.dependencies['run'].update(
                        create_dependency_descriptor(d)
                        for d in options.get('install_requires') or ())
                if 'optional-dependencies' in dynamic_fields:
                    desc.dependencies['test'].update(
                        create_dependency_descriptor(d)
                        for d in (
                            options.get('extras_require') or {}
                        ).get('test') or ())
                if 'version' in dynamic_fields and metadata.get('Version'):
                    desc.metadata['version'] = metadata['Version']
                if {'authors', 'maintainers'} & dynamic_fields:
                    _augment_maintainers_from_metadata(desc, metadata)
