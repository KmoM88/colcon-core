# Copyright 2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

"""Tests for PEP621PackageAugmentation."""

from email.message import Message
from unittest.mock import patch

from colcon_core.package_augmentation.pep621 import PEP621PackageAugmentation
from colcon_core.package_descriptor import PackageDescriptor
from colcon_core.task.python import get_setup_data
from colcon_core.task.python.test import has_test_dependency


def test_augment_pep621_authors(tmp_path):
    """Test PEP621PackageAugmentation with authors."""
    ext = PEP621PackageAugmentation()

    (tmp_path / 'pyproject.toml').write_text(
        '[build-system]\n'
        'requires = ["flit_core >=3.2,<4"]\n'
        'build-backend = "flit_core.build"\n'
        '[project]\n'
        'name = "my-pkg"\n'
        'version = "1.0.0"\n'
        'dependencies = ["requests >= 2.0"]\n'
        '[project.optional-dependencies]\n'
        'test = ["pytest"]\n'
        '[[project.authors]]\n'
        'name = "Foo Bar"\n'
        'email = "foobar@example.com"\n'
    )

    desc = PackageDescriptor(str(tmp_path))
    desc.name = 'my-pkg'
    desc.type = 'python.project'

    ext.augment_package(desc)

    assert desc.dependencies['build'] == {'flit_core'}
    assert desc.dependencies['run'] == {'requests'}
    assert desc.dependencies['test'] == {'pytest'}
    assert desc.metadata['version'] == '1.0.0'
    assert 'Foo Bar <foobar@example.com>' in desc.metadata['maintainers']


def test_augment_pep621_maintainers(tmp_path):
    """Test PEP621PackageAugmentation with maintainers."""
    ext = PEP621PackageAugmentation()

    (tmp_path / 'pyproject.toml').write_text(
        '[project]\n'
        'name = "my-pkg"\n'
        '[[project.maintainers]]\n'
        'name = "No Email"\n'
        '[[project.maintainers]]\n'
        'email = "no-name@example.com"\n'
    )

    desc = PackageDescriptor(str(tmp_path))
    desc.name = 'my-pkg'
    desc.type = 'python.project'

    ext.augment_package(desc)

    assert 'no-name@example.com' in desc.metadata['maintainers']


def test_augment_missing_spec_file(tmp_path):
    """Test package augmentation when pyproject.toml is missing."""
    ext = PEP621PackageAugmentation()

    desc = PackageDescriptor(str(tmp_path))
    desc.name = 'my-pkg'
    desc.type = 'python.project'

    ext.augment_package(desc)
    assert not desc.dependencies


def test_augment_ignore_non_python_project(tmp_path):
    """Test that non-python.project packages are ignored."""
    ext = PEP621PackageAugmentation()
    desc = PackageDescriptor(str(tmp_path))
    desc.type = 'python'
    ext.augment_package(desc)
    assert not desc.dependencies


def test_augment_pep621_setup_options_and_dependency_groups(tmp_path):
    """Test get_python_setup_options and PEP 735 dependency-groups support."""
    ext = PEP621PackageAugmentation()

    (tmp_path / 'pyproject.toml').write_text(
        '[project]\n'
        'name = "my-pkg"\n'
        'version = "1.0.0"\n'
        'dependencies = ["requests >= 2.0"]\n'
        '[project.optional-dependencies]\n'
        'testing = ["pytest-mock"]\n'
        '[dependency-groups]\n'
        'base_test = ["pytest >= 7.0", {include-group = "test"}]\n'
        'test = [{include-group = "base-test"}, "pytest-cov"]\n',
        encoding='utf-8',
    )

    desc = PackageDescriptor(str(tmp_path))
    desc.name = 'my-pkg'
    desc.type = 'python.project'

    ext.augment_package(desc)

    assert desc.dependencies['run'] == {'requests'}
    assert desc.dependencies['test'] == {
        'pytest',
        'pytest-cov',
        'pytest-mock',
    }

    setup_data = get_setup_data(desc, {})
    assert has_test_dependency(setup_data, 'pytest')
    assert has_test_dependency(setup_data, 'pytest-cov')
    assert has_test_dependency(setup_data, 'pytest-mock')


def test_augment_static_never_invokes_dynamic_hook(tmp_path):
    """Static PEP 621 manifests must not spawn a metadata hook subprocess."""
    ext = PEP621PackageAugmentation()
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\n'
        'name = "static-pkg"\n'
        'version = "1.0.0"\n',
        encoding='utf-8',
    )
    desc = PackageDescriptor(str(tmp_path))
    desc.name = 'static-pkg'
    desc.type = 'python.project'

    with patch(
        'colcon_core.package_augmentation.pep621.load_and_cache_metadata'
    ) as load_mock:
        ext.augment_package(desc)

    assert load_mock.call_count == 0
    assert desc.metadata['version'] == '1.0.0'


def test_augment_dynamic_metadata_and_error_fallback(tmp_path):
    """Dynamic PEP 621 fields populate from metadata and warn on errors."""
    ext = PEP621PackageAugmentation()
    (tmp_path / 'pyproject.toml').write_text(
        '[build-system]\n'
        'requires = ["setuptools"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[project]\n'
        'name = "dyn-pkg"\n'
        'dynamic = [\n'
        '  "version",\n'
        '  "dependencies",\n'
        '  "optional-dependencies",\n'
        '  "maintainers",\n'
        ']\n',
        encoding='utf-8',
    )

    fake_md = Message()
    fake_md['Version'] = '4.5.6'
    fake_md['Requires-Dist'] = 'requests>=2.0'
    fake_md['Requires-Dist'] = "pytest>=7.0 ; extra == 'test'"
    fake_md['Maintainer-email'] = (
        'Foo Bar <foobar@example.com>, ops@example.com'
    )

    async def _fake_load(_desc):
        return fake_md

    desc = PackageDescriptor(str(tmp_path))
    desc.name = 'dyn-pkg'
    desc.type = 'python.project'

    with patch(
        'colcon_core.package_augmentation.pep621.load_and_cache_metadata',
        side_effect=_fake_load,
    ):
        ext.augment_package(desc)

    assert desc.metadata['version'] == '4.5.6'
    assert desc.dependencies['run'] == {'requests'}
    assert desc.dependencies['test'] == {'pytest'}
    assert desc.metadata['maintainers'] == [
        'Foo Bar <foobar@example.com>',
        'ops@example.com',
    ]

    # Failure in backend hook logs warning without aborting augmentation
    async def _failing_load(_desc):
        raise RuntimeError('backend missing')

    desc_err = PackageDescriptor(str(tmp_path))
    desc_err.name = 'dyn-pkg'
    desc_err.type = 'python.project'
    with patch(
        'colcon_core.package_augmentation.pep621.load_and_cache_metadata',
        side_effect=_failing_load,
    ), patch(
        'colcon_core.package_augmentation.pep621.logger.warning'
    ) as warn_mock:
        ext.augment_package(desc_err)

    assert warn_mock.call_count == 1
    assert desc_err.dependencies['build'] == {'setuptools'}
