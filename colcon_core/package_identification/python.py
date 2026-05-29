# Copyright 2016-2019 Dirk Thomas
# Licensed under the Apache License, Version 2.0

import warnings

from colcon_core.package_identification import logger
from colcon_core.package_identification \
    import PackageIdentificationExtensionPoint
from colcon_core.plugin_system import satisfies_version


class PythonPackageIdentification(PackageIdentificationExtensionPoint):
    """
    Identify Python packages with `setup.cfg` files.

    Only packages which pass no arguments (or only a ``cmdclass``) to the
    ``setup()`` function in their ``setup.py`` file are being considered.
    """

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(
            PackageIdentificationExtensionPoint.EXTENSION_POINT_VERSION,
            '^1.0')

    def identify(self, desc):  # noqa: D102
        if desc.type is not None and desc.type != 'python':
            return

        # 1. Try PEP 517 pyproject.toml package identification first
        pyproject_toml = desc.path / 'pyproject.toml'
        if pyproject_toml.is_file():
            try:
                from colcon_core.python_project.spec import toml_loads
                config = toml_loads(pyproject_toml.read_text())
                if 'build-system' in config or 'project' in config:
                    self._identify_pep517(desc, config)
                    if desc.type == 'python':
                        return
            except Exception as e:  # noqa: B902
                logger.warning(f'Failed to parse pyproject.toml: {e}')

        # 2. Check Feature Gate for Legacy setup.cfg fallback
        import os
        enable_legacy = os.environ.get(
            'COLCON_ENABLE_LEGACY_SETUP_CFG', '1'
        ).lower() in ('1', 'true', 'on')

        # 3. Fallback to Legacy setup.cfg logic
        if enable_legacy:
            setup_py = desc.path / 'setup.py'
            if not setup_py.is_file():
                return

            setup_cfg = desc.path / 'setup.cfg'
            if not setup_cfg.is_file():
                return

            if not is_reading_cfg_sufficient(setup_py):
                logger.debug(
                    f"Python package in '{desc.path}' passes arguments to the "
                    'setup() function which requires a '
                    'different identification '
                    f"extension than '{self.PACKAGE_IDENTIFICATION_NAME}'")
                return

            config = get_configuration(setup_cfg)
            name = config.get('metadata', {}).get('name')
            if not name:
                return

            desc.type = 'python'
            if desc.name is not None and desc.name != name:
                msg = 'Package name already set to different value'
                logger.error(msg)
                raise RuntimeError(msg)
            desc.name = name

    def _identify_pep517(self, desc, config):
        name = config.get('project', {}).get('name')

        if not name:
            setup_cfg = desc.path / 'setup.cfg'
            if setup_cfg.is_file():
                try:
                    cfg = get_configuration(setup_cfg)
                    name = cfg.get('metadata', {}).get('name')
                except Exception:  # noqa: B902
                    pass

        if not name:
            setup_py = desc.path / 'setup.py'
            setup_cfg = desc.path / 'setup.cfg'
            if (
                setup_py.is_file() and
                is_reading_cfg_sufficient(setup_py) and
                setup_cfg.is_file()
            ):
                try:
                    cfg = get_configuration(setup_cfg)
                    name = cfg.get('metadata', {}).get('name')
                except Exception:  # noqa: B902
                    pass

        if not name:
            return

        desc.type = 'python'
        if desc.name is not None and desc.name != name:
            msg = 'Package name already set to different value'
            logger.error(msg)
            raise RuntimeError(msg)
        desc.name = name

        # Cache parsed pyproject.toml as python_project_spec
        desc.metadata['python_project_spec'] = config


def is_reading_cfg_sufficient(setup_py):
    """
    Check the content of the setup.py file.

    If the ``setup()`` function is called with no arguments or only a
    ``cmdclass`` it is sufficient to only read the content of the ``setup.cfg``
    file.

    :param setup_py: The path of the setup.py file
    :returns: The flag if reading the setup.cfg file is sufficient
    :rtype: bool
    """
    setup_py_content = setup_py.read_text()
    # the setup function must be called with no arguments
    # or only a ``cmdclass``to be considered by this extension otherwise
    # only reading the content of the setup.cfg file isn't sufficient
    return 'setup()' in setup_py_content or \
        'setup(cmdclass=cmdclass)' in setup_py_content


def get_configuration(setup_cfg):
    """
    Read the setup.cfg file.

    :param setup_cfg: The path of the setup.cfg file
    :returns: The configuration data
    :rtype: dict
    """
    try:
        # import locally to allow other functions in this module to be usable
        try:
            from setuptools.config.setupcfg import read_configuration
        except ImportError:
            from setuptools.config import read_configuration
    except ImportError as e:
        try:
            from importlib.metadata import distribution
        except ImportError:
            from importlib_metadata import distribution
        from packaging.version import Version
        try:
            setuptools_version = distribution('setuptools').version
        except ModuleNotFoundError:
            setuptools_version = '0'
        minimum_version = '30.3.0'
        if Version(setuptools_version) < Version(minimum_version):
            e.msg += ', ' \
                "'setuptools' needs to be at least version " \
                f'{minimum_version}, if a newer version is not available ' \
                "from the package manager use 'pip3 install -U setuptools' " \
                'to update to the latest version'
        raise
    return read_configuration(str(setup_cfg))


def extract_dependencies(options):
    """
    Get the dependencies of the package.

    This function has been depreated, use
    ``colcon_core.package_augmentation.python.extract_dependencies()``
    instead.

    :param options: The dictionary from the options section of the setup.cfg
      file
    :returns: The dependencies
    :rtype: dict(string, set(DependencyDescriptor))
    """
    warnings.warn(
        "'colcon_core.package_identification.python.extract_dependencies()' "
        'has been deprecated, use '
        "'colcon_core.package_augmentation.python.extract_dependencies()' "
        'instead', stacklevel=2)
    from colcon_core.package_augmentation.python import \
        extract_dependencies as function
    return function(options)


def create_dependency_descriptor(requirement_string):
    """
    Create a DependencyDescriptor from a PEP440 compliant string.

    See https://www.python.org/dev/peps/pep-0440/#version-specifiers

    This function has been depreated, use
    ``colcon_core.package_augmentation.python.create_dependency_descriptor()``
    instead.

    :param str requirement_string: a PEP440 compliant requirement string
    :return: A descriptor with version constraints from the requirement string
    :rtype: DependencyDescriptor
    """
    warnings.warn(
        "'colcon_core.package_identification.python."
        "create_dependency_descriptor()' has been deprecated, use "
        "'colcon_core.package_augmentation.python."
        "create_dependency_descriptor()' instead", stacklevel=2)
    from colcon_core.package_augmentation.python import \
        create_dependency_descriptor as function
    return function(requirement_string)
