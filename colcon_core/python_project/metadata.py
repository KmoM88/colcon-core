# Copyright 2022-2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

"""Metadata helpers for python.project packages."""

from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from colcon_core.python_project.hook_caller_decorator \
    import get_decorated_hook_caller
from distlib.util import parse_requirement

try:
    from importlib.metadata import Distribution
except ImportError:
    from importlib_metadata import Distribution


TEST_EXTRAS = (
    "'test'", '"test"',
    "'tests'", '"tests"',
    "'testing'", '"testing"',
)


async def load_metadata(desc):
    """
    Load metadata for a Python project using PEP 517.

    Uses ``prepare_metadata_for_build_wheel`` when implemented by the build
    backend, falling back to ``build_wheel`` per PEP 517 otherwise.

    :param desc: The package descriptor
    """
    hook_caller = get_decorated_hook_caller(desc)
    hooks = await hook_caller.list_hooks()
    with TemporaryDirectory() as md_dir:
        if 'prepare_metadata_for_build_wheel' in hooks:
            md_name = await hook_caller.call_hook(
                'prepare_metadata_for_build_wheel', metadata_directory=md_dir)
            md_path = Path(md_dir) / md_name
            return Distribution.at(md_path).metadata

        wheel_name = await hook_caller.call_hook(
            'build_wheel', wheel_directory=md_dir)
        wheel_path = Path(md_dir) / wheel_name
        extract_dir = Path(md_dir) / 'extracted'
        with ZipFile(wheel_path) as wf:
            for member in wf.namelist():
                if '.dist-info/' in member:
                    wf.extract(member, extract_dir)
        dist_info_dirs = list(extract_dir.glob('*.dist-info'))
        if not dist_info_dirs:
            raise RuntimeError(
                f"Built wheel '{wheel_name}' contains no .dist-info directory")
        return Distribution.at(dist_info_dirs[0]).metadata


async def load_and_cache_metadata(desc):
    """
    Get the cached metadata for a package descriptor.

    If the metadata has not been loaded yet, load and cache it.

    :param desc: The package descriptor
    """
    get_metadata = desc.metadata.get('get_python_project_metadata')
    if get_metadata is None:
        metadata = await load_metadata(desc)

        def get_metadata():
            return metadata

        desc.metadata['get_python_project_metadata'] = get_metadata

        options = options_from_metadata(metadata)

        def get_options(_):
            return options

        desc.metadata['get_python_setup_options'] = get_options
    return get_metadata()


def options_from_metadata(metadata):
    """
    Extract select information from metadata in setuptools options format.

    :param metadata: The package metadata
    :returns: Setuptools options
    :rtype: dict
    """
    install_deps = []
    test_deps = []

    for raw_req in metadata.get_all('Requires-Dist', ()):
        req = parse_requirement(raw_req)
        if req.marker:
            if (
                req.marker['lhs'] == 'extra' and
                req.marker['op'] == '==' and
                req.marker['rhs'] in TEST_EXTRAS
            ):
                test_deps.append(req.requirement)
        else:
            install_deps.append(req.requirement)

    options = {}
    if install_deps:
        options['install_requires'] = install_deps
    if test_deps:
        options['extras_require'] = {
            'test': test_deps,
        }

    return options
