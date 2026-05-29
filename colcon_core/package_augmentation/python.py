# Copyright 2016-2019 Dirk Thomas
# Licensed under the Apache License, Version 2.0

from colcon_core.dependency_descriptor import DependencyDescriptor
from colcon_core.package_augmentation import logger
from colcon_core.package_augmentation \
    import PackageAugmentationExtensionPoint
from colcon_core.package_identification.python import get_configuration
from colcon_core.package_identification.python import is_reading_cfg_sufficient
from colcon_core.plugin_system import satisfies_version
from distlib.util import parse_requirement
from distlib.version import NormalizedVersion


class PythonPackageAugmentation(PackageAugmentationExtensionPoint):
    """
    Augment Python packages with information from `setup.cfg` files.

    Only packages which pass no arguments (or only a ``cmdclass``) to the
    ``setup()`` function in their ``setup.py`` file are being considered.
    """

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            PackageAugmentationExtensionPoint.EXTENSION_POINT_VERSION,
            '^1.0')

    def augment_package(  # noqa: D102
        self, desc, *, additional_argument_names=None
    ):
        if desc.type != 'python':
            return

        # 1. Try PEP 517 pyproject.toml package augmentation first
        pyproject_toml = desc.path / 'pyproject.toml'
        if pyproject_toml.is_file():
            try:
                from colcon_core.python_project.spec import toml_loads
                config = toml_loads(pyproject_toml.read_text())
                if 'build-system' in config or 'project' in config:
                    self._augment_pep517(desc, config)
                    return
            except Exception as e:
                logger.warning(f"Failed to parse pyproject.toml: {e}")

        # 2. Check Feature Gate for Legacy setup.cfg fallback
        import os
        enable_legacy = os.environ.get('COLCON_ENABLE_LEGACY_SETUP_CFG', '1').lower() in ('1', 'true', 'on')

        # 3. Fallback to Legacy setup.cfg logic
        if enable_legacy:
            setup_py = desc.path / 'setup.py'
            if not setup_py.is_file():
                return

            setup_cfg = desc.path / 'setup.cfg'
            if not setup_cfg.is_file():
                return

            if not is_reading_cfg_sufficient(setup_py):
                return

            config = get_configuration(setup_cfg)

            metadata = config.get('metadata', {})
            version = metadata.get('version')
            desc.metadata['version'] = version

            options = config.get('options', {})
            dependencies = extract_dependencies(options)
            for k, v in dependencies.items():
                desc.dependencies[k] |= v

            def getter(env):
                return options

            desc.metadata['get_python_setup_options'] = getter

            maintainers = _extract_maintainers_with_emails(metadata)
            if maintainers:
                desc.metadata.setdefault('maintainers', [])
                desc.metadata['maintainers'] += maintainers

    def _augment_pep517(self, desc, config):
        project = config.get('project', {})
        version = project.get('version')
        if version:
            desc.metadata['version'] = version

        dependencies = extract_pep517_dependencies(config)
        for k, v in dependencies.items():
            desc.dependencies[k] |= v

        maintainers = []
        for author in project.get('authors', []):
            name = author.get('name')
            email = author.get('email')
            if name and email:
                maintainers.append(f"{name} <{email}>")
        for maintainer in project.get('maintainers', []):
            name = maintainer.get('name')
            email = maintainer.get('email')
            if name and email:
                maintainers.append(f"{name} <{email}>")

        if maintainers:
            desc.metadata.setdefault('maintainers', [])
            desc.metadata['maintainers'] += maintainers


def extract_pep517_dependencies(config):
    """
    Get the dependencies of the PEP 517 package from pyproject.toml.

    :param config: The parsed pyproject.toml dict
    :returns: The dependencies
    :rtype: dict(string, set(DependencyDescriptor))
    """
    dependencies = {
        'build': set(),
        'run': set(),
        'test': set(),
    }

    # 1. Build dependencies from [build-system] requires (PEP 518)
    build_system = config.get('build-system', {})
    for dep in build_system.get('requires', []):
        try:
            dependencies['build'].add(create_dependency_descriptor(dep))
        except Exception:
            pass

    # 2. Run dependencies from [project] dependencies (PEP 621)
    project = config.get('project', {})
    for dep in project.get('dependencies', []):
        try:
            dependencies['run'].add(create_dependency_descriptor(dep))
        except Exception:
            pass

    # 3. Test dependencies from [project.optional-dependencies] extra groups (test, tests, testing)
    optional = project.get('optional-dependencies', {})
    for extra_group, deps in optional.items():
        if extra_group in ('test', 'tests', 'testing'):
            for dep in deps:
                try:
                    dependencies['test'].add(create_dependency_descriptor(dep))
                except Exception:
                    pass

    return dependencies


def extract_dependencies(options):
    """
    Get the dependencies of the package.

    :param options: The dictionary from the options section of the setup.cfg
      file
    :returns: The dependencies
    :rtype: dict(string, set(DependencyDescriptor))
    """
    mapping = {
        'setup_requires': 'build',
        'install_requires': 'run',
        'tests_require': 'test',
    }
    dependencies = {}
    _map_dependencies(options, mapping, dependencies)

    extras_mapping = {
        'test': 'test',
        'tests': 'test',
        'testing': 'test',
    }
    _map_dependencies(
        options.get('extras_require') or {}, extras_mapping,
        dependencies)

    return dependencies


def _map_dependencies(options, mapping, dependencies):
    for option_name, dependency_type in mapping.items():
        dependencies.setdefault(dependency_type, set())
        for dep in options.get(option_name) or []:
            dependencies[dependency_type].add(
                create_dependency_descriptor(dep))


def create_dependency_descriptor(requirement_string):
    """
    Create a DependencyDescriptor from a PEP440 compliant string.

    See https://www.python.org/dev/peps/pep-0440/#version-specifiers

    :param str requirement_string: a PEP440 compliant requirement string
    :return: A descriptor with version constraints from the requirement string
    :rtype: DependencyDescriptor
    """
    symbol_mapping = {
        '==': 'version_eq',
        '!=': 'version_neq',
        '<=': 'version_lte',
        '>=': 'version_gte',
        '>': 'version_gt',
        '<': 'version_lt',
    }

    # Drop inline comments
    requirement_string = requirement_string.partition(' #')[0]

    requirement = parse_requirement(requirement_string)
    metadata = {
        'origin': 'python',
    }
    for symbol, version in (requirement.constraints or []):
        if symbol in symbol_mapping:
            metadata[symbol_mapping[symbol]] = version
        elif symbol == '~=':
            metadata['version_gte'] = version
            metadata['version_lt'] = _next_incompatible_version(version)
        else:
            logger.warning(
                f"Ignoring unknown symbol '{symbol}' in '{requirement}'")
    return DependencyDescriptor(requirement.name, metadata=metadata)


def _next_incompatible_version(version):
    """
    Find the next non-compatible version.

    This is for use with the ~= compatible syntax. It will provide
    the first version that this version must be less than in order
    to be compatible.

    :param str version: PEP 440 compliant version number
    :return: The first version after this version that is not compatible
    :rtype: str
    """
    normalized = NormalizedVersion(version)
    parse_tuple = normalized.parse(version)
    version_tuple = parse_tuple[1]

    *unchanged, increment, dropped = version_tuple
    incremented = increment + 1

    version = unchanged
    version.append(incremented)
    # versions have a minimum length of 2
    if len(version) == 1:
        version.append(0)
    return '.'.join(map(str, version))


def _extract_maintainers_with_emails(metadata):
    if 'maintainer' in metadata:
        maintainer = metadata['maintainer']
        maintainer_email = metadata.get('maintainer_email')
    else:
        # If no explicit maintainer is given then it is likely that the
        # original author is maintaining the package following python
        # recommendations
        # https://packaging.python.org/en/latest/specifications/core-metadata/#maintainer
        maintainer = metadata.get('author')
        maintainer_email = metadata.get('author_email')

    # We're only interested in entries with emails
    if maintainer and maintainer_email:
        maintainers = [
            (m[0].strip(), m[1].strip()) for m in zip(
                maintainer.split(','),
                maintainer_email.split(','))]
        return ['{} <{}>'.format(*m) for m in maintainers]
