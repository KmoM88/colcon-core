# Copyright 2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

"""Integration tests for PEP 621 packages and build backends."""

import importlib
import json
import os
import site
import subprocess
import sys
from types import SimpleNamespace

from colcon_core import plugin_system
from colcon_core.extension_point import clear_entry_point_cache
from colcon_core.package_augmentation.pep621 \
    import PEP621PackageAugmentation
from colcon_core.package_descriptor import PackageDescriptor
from colcon_core.package_identification.pep621 \
    import PEP621PackageIdentification
from colcon_core.python_project.wheel import remove_distributions
from colcon_core.task import TaskContext
from colcon_core.task.python_project.build import PythonProjectBuildTask
from colcon_core.task.python_project.test import PythonProjectTestTask
from packaging.version import Version
import pytest

from .run_until_complete import run_until_complete


@pytest.fixture(autouse=True)
def isolate_extension_cache_and_event_queue(monkeypatch):
    """Clear cached entry points and ignore direct task output events."""
    clear_entry_point_cache()
    plugin_system._extension_instances.clear()
    monkeypatch.setattr(
        TaskContext, 'put_event_into_queue', lambda *args: None
    )


def test_integration_flit_core_minimal(tmp_path):
    """Test full build life cycle of a pure PEP 621 package with flit_core."""
    pytest.importorskip('flit_core')

    pkg_dir = tmp_path / 'src' / 'flit_pkg'
    pkg_dir.mkdir(parents=True)

    (pkg_dir / 'pyproject.toml').write_text("""
[build-system]
requires = ["flit_core >=3.2,<4"]
build-backend = "flit_core.buildapi"

[project]
name = "flit_pkg"
version = "0.1.0"
description = "A minimal flit package"
readme = "README.md"
requires-python = ">=3.8"
dependencies = []
""")
    (pkg_dir / 'README.md').write_text('# flit_pkg\n')
    mod_dir = pkg_dir / 'flit_pkg'
    mod_dir.mkdir()
    (mod_dir / '__init__.py').write_text(
        '"""Flit package."""\n__version__ = "0.1.0"\n'
        'def greet(name):\n    return f"Hello, {name}!"\n'
    )

    # 1. Identification
    ident = PEP621PackageIdentification()
    desc = PackageDescriptor(pkg_dir)
    ident.identify(desc)
    assert desc.type == 'python.project'
    assert desc.name == 'flit_pkg'

    # 2. Augmentation
    aug = PEP621PackageAugmentation()
    aug.augment_package(desc)
    assert desc.metadata.get('version') == '0.1.0'

    # 3. Build task
    build_base = tmp_path / 'build' / 'flit_pkg'
    install_base = tmp_path / 'install'
    build_base.mkdir(parents=True)
    install_base.mkdir(parents=True)

    task = PythonProjectBuildTask()
    context = TaskContext(
        pkg=desc,
        args=SimpleNamespace(
            path=str(pkg_dir),
            build_base=str(build_base),
            install_base=str(install_base),
            symlink_install=False,
        ),
        dependencies={},
    )
    task.set_context(context=context)

    res = run_until_complete(task.build())
    assert res is None, f'Build task failed with result: {res}'

    # 4. Verify output files
    wheels = list((build_base / 'wheel').glob('*.whl'))
    assert len(wheels) == 1
    assert 'flit_pkg-0.1.0' in wheels[0].name

    # Check installed package files
    dist_info = list(install_base.glob('**/flit_pkg-0.1.0.dist-info'))
    assert len(dist_info) == 1
    dist_dir = dist_info[0]
    lib_dir = dist_dir.parent

    assert (lib_dir / 'flit_pkg' / '__init__.py').is_file()
    assert (dist_dir / 'RECORD').is_file()
    assert (dist_dir / 'INSTALLER').is_file()
    assert 'colcon-core' in (dist_dir / 'INSTALLER').read_text()

    # 5. Verify module import
    sys.path.insert(0, str(lib_dir))
    try:
        import flit_pkg
        importlib.reload(flit_pkg)
        assert flit_pkg.greet('World') == 'Hello, World!'
    finally:
        sys.path.remove(str(lib_dir))


def test_integration_hatchling_cli_scripts(tmp_path):
    """Test console scripts entry points built using hatchling."""
    pytest.importorskip('hatchling')

    pkg_dir = tmp_path / 'src' / 'hatch_cli_pkg'
    pkg_dir.mkdir(parents=True)

    (pkg_dir / 'pyproject.toml').write_text("""
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "hatch_cli_pkg"
version = "1.2.3"
description = "CLI demo using hatchling"
dependencies = []

[project.scripts]
hatch-tool = "hatch_cli_pkg.cli:main"
""")
    src_dir = pkg_dir / 'src' / 'hatch_cli_pkg'
    src_dir.mkdir(parents=True)
    (src_dir / '__init__.py').write_text('__version__ = "1.2.3"\n')
    (src_dir / 'cli.py').write_text(
        'def main():\n'
        '    print("HATCH_CLI_SUCCESS")\n'
        '    return 0\n'
    )

    # 1. Identification
    ident = PEP621PackageIdentification()
    desc = PackageDescriptor(pkg_dir)
    ident.identify(desc)
    assert desc.type == 'python.project'

    # 2. Build task
    build_base = tmp_path / 'build' / 'hatch_cli_pkg'
    install_base = tmp_path / 'install'
    build_base.mkdir(parents=True)
    install_base.mkdir(parents=True)

    task = PythonProjectBuildTask()
    context = TaskContext(
        pkg=desc,
        args=SimpleNamespace(
            path=str(pkg_dir),
            build_base=str(build_base),
            install_base=str(install_base),
            symlink_install=False,
        ),
        dependencies={},
    )
    task.set_context(context=context)

    res = run_until_complete(task.build())
    assert res is None

    # Check entry point script in bin directory
    bin_dir = install_base / 'bin'
    if not bin_dir.exists():
        bin_dir = install_base / 'Scripts'
    assert bin_dir.is_dir()

    script_path = bin_dir / 'hatch-tool'
    if not script_path.exists():
        script_path = bin_dir / 'hatch-tool.exe'
    assert script_path.is_file()

    # Ensure executable bit
    assert os.access(script_path, os.X_OK)

    # Execute the generated script wrapper
    dist_info = list(install_base.glob('**/hatch_cli_pkg-1.2.3.dist-info'))[0]
    lib_dir = dist_info.parent
    env = {
        **os.environ,
        'PYTHONPATH': os.pathsep.join(
            [str(lib_dir), os.environ.get('PYTHONPATH', '')]
        ),
    }

    cmd = [sys.executable, str(script_path)]
    proc = subprocess.run(
        cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert proc.returncode == 0
    assert 'HATCH_CLI_SUCCESS' in proc.stdout


def test_integration_clean_rebuild(tmp_path):
    """Test repeated clean rebuilds with remove_distributions."""
    pytest.importorskip('flit_core')

    pkg_dir = tmp_path / 'src' / 'rebuild_pkg'
    pkg_dir.mkdir(parents=True)

    (pkg_dir / 'pyproject.toml').write_text("""
[build-system]
requires = ["flit_core >=3.2,<4"]
build-backend = "flit_core.buildapi"

[project]
name = "rebuild_pkg"
version = "0.1.0"
description = "Rebuild test package"
dependencies = []
""")
    mod_dir = pkg_dir / 'rebuild_pkg'
    mod_dir.mkdir()
    (mod_dir / '__init__.py').write_text('__version__ = "0.1.0"\n')
    (mod_dir / 'extra_feature.py').write_text('def extra(): return 42\n')

    desc = PackageDescriptor(pkg_dir)
    PEP621PackageIdentification().identify(desc)

    build_base = tmp_path / 'build'
    install_base = tmp_path / 'install'
    build_base.mkdir(parents=True)
    install_base.mkdir(parents=True)

    # Initial build
    task = PythonProjectBuildTask()
    context = TaskContext(
        pkg=desc,
        args=SimpleNamespace(
            path=str(pkg_dir),
            build_base=str(build_base),
            install_base=str(install_base),
            symlink_install=False,
        ),
        dependencies={},
    )
    task.set_context(context=context)
    assert run_until_complete(task.build()) is None

    dist_info = list(install_base.glob('**/rebuild_pkg-0.1.0.dist-info'))[0]
    lib_dir = dist_info.parent
    extra_file = lib_dir / 'rebuild_pkg' / 'extra_feature.py'
    assert extra_file.is_file()

    # Remove extra_feature.py from source
    (mod_dir / 'extra_feature.py').unlink()

    # Clean remove existing distribution
    remove_distributions('rebuild_pkg', install_base)
    assert not extra_file.exists()
    assert not dist_info.exists()

    # Rebuild
    task2 = PythonProjectBuildTask()
    task2.set_context(context=context)
    assert run_until_complete(task2.build()) is None

    # Verify extra_feature.py is no longer present
    assert not extra_file.exists()
    assert (lib_dir / 'rebuild_pkg' / '__init__.py').is_file()


def test_integration_symlink_install_editable(tmp_path):
    """Test --symlink-install with hatchling and build_editable."""
    pytest.importorskip('hatchling')
    pytest.importorskip('editables')

    pkg_dir = tmp_path / 'src' / 'editable_pkg'
    pkg_dir.mkdir(parents=True)

    (pkg_dir / 'pyproject.toml').write_text("""
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "editable_pkg"
version = "0.1.0"
dependencies = []
""")
    src_dir = pkg_dir / 'src' / 'editable_pkg'
    src_dir.mkdir(parents=True)
    init_file = src_dir / '__init__.py'
    init_file.write_text(
        '__version__ = "0.1.0"\n'
        'def value(): return "INITIAL"\n'
    )

    desc = PackageDescriptor(pkg_dir)
    PEP621PackageIdentification().identify(desc)

    build_base = tmp_path / 'build'
    install_base = tmp_path / 'install'
    build_base.mkdir(parents=True)
    install_base.mkdir(parents=True)

    task = PythonProjectBuildTask()
    context = TaskContext(
        pkg=desc,
        args=SimpleNamespace(
            path=str(pkg_dir),
            build_base=str(build_base),
            install_base=str(install_base),
            symlink_install=True,
        ),
        dependencies={},
    )
    task.set_context(context=context)
    res = run_until_complete(task.build())
    assert res is None

    # Check PEP 610 direct_url.json
    dist_info = list(install_base.glob('**/editable_pkg-0.1.0.dist-info'))[0]
    direct_url = dist_info / 'direct_url.json'
    assert direct_url.is_file()
    data = json.loads(direct_url.read_text())
    assert data.get('dir_info', {}).get('editable') is True

    # Verify dynamic updates in editable mode via site.addsitedir
    lib_dir = dist_info.parent
    site.addsitedir(str(lib_dir))
    try:
        import editable_pkg
        importlib.reload(editable_pkg)
        assert editable_pkg.value() == 'INITIAL'

        # Update source without rebuild
        init_file.write_text(
            '__version__ = "0.1.0"\n'
            'def value(): return "EDITED_LIVE"\n'
        )
        importlib.reload(editable_pkg)
        assert editable_pkg.value() == 'EDITED_LIVE'
    finally:
        if str(lib_dir) in sys.path:
            sys.path.remove(str(lib_dir))


def test_integration_symlink_install_data_files(tmp_path):
    """Test --symlink-install data files parity and unique RECORD entries."""
    pytest.importorskip('hatchling')
    pytest.importorskip('editables')

    pkg_dir = tmp_path / 'src' / 'data_pkg'
    pkg_dir.mkdir(parents=True)

    (pkg_dir / 'pyproject.toml').write_text("""
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "data_pkg"
version = "0.1.0"
description = "Data files test package"
dependencies = []

[tool.hatch.build.targets.wheel.shared-data]
"launch" = "share/data_pkg/launch"
"config/settings.yaml" = "share/data_pkg/config/settings.yaml"
""")
    mod_dir = pkg_dir / 'data_pkg'
    mod_dir.mkdir()
    (mod_dir / '__init__.py').write_text('__version__ = "0.1.0"\n')

    launch_dir = pkg_dir / 'launch'
    launch_dir.mkdir()
    launch_file = launch_dir / 'my_launch.py'
    launch_file.write_text('LAUNCH_ORIGINAL = True\n')

    cfg_dir = pkg_dir / 'config'
    cfg_dir.mkdir()
    cfg_file = cfg_dir / 'settings.yaml'
    cfg_file.write_text('key: original\n')

    desc = PackageDescriptor(pkg_dir)
    PEP621PackageIdentification().identify(desc)

    build_base = tmp_path / 'build'
    install_base = tmp_path / 'install'
    build_base.mkdir(parents=True)
    install_base.mkdir(parents=True)

    # 1. Standard build installs data files as regular files
    task = PythonProjectBuildTask()
    context_regular = TaskContext(
        pkg=desc,
        args=SimpleNamespace(
            path=str(pkg_dir),
            build_base=str(build_base),
            install_base=str(install_base),
            symlink_install=False,
        ),
        dependencies={},
    )
    task.set_context(context=context_regular)
    assert run_until_complete(task.build()) is None

    installed_launch = (
        install_base / 'share' / 'data_pkg' / 'launch' / 'my_launch.py'
    )
    installed_cfg = (
        install_base / 'share' / 'data_pkg' / 'config' / 'settings.yaml'
    )
    assert installed_launch.is_file() and not installed_launch.is_symlink()
    assert installed_cfg.is_file() and not installed_cfg.is_symlink()

    # 2. Editable build replaces data files with symlinks without RECORD dupes
    task_editable = PythonProjectBuildTask()
    context_editable = TaskContext(
        pkg=desc,
        args=SimpleNamespace(
            path=str(pkg_dir),
            build_base=str(build_base),
            install_base=str(install_base),
            symlink_install=True,
        ),
        dependencies={},
    )
    task_editable.set_context(context=context_editable)
    assert run_until_complete(task_editable.build()) is None

    assert installed_launch.is_symlink()
    assert installed_launch.resolve() == launch_file.resolve()
    assert installed_cfg.is_symlink()
    assert installed_cfg.resolve() == cfg_file.resolve()

    # Verify RECORD contains no duplicate path entries
    dist_info = list(install_base.glob('**/data_pkg-0.1.0.dist-info'))[0]
    record_lines = [
        line.split(',', 1)[0]
        for line in (dist_info / 'RECORD').read_text().splitlines()
        if ',' in line
    ]
    assert len(record_lines) == len(set(record_lines))

    # Edit launch file in source without rebuilding
    launch_file.write_text('LAUNCH_EDITED_LIVE = True\n')
    assert 'LAUNCH_EDITED_LIVE = True' in installed_launch.read_text()


def test_integration_setuptools_data_files_and_script_override(tmp_path):
    """Test setuptools data-files globs and explicit script_dir_override."""
    st = pytest.importorskip('setuptools', minversion='64.0.0')
    if Version(st.__version__) < Version('70.1.0'):
        pytest.importorskip('wheel')

    pkg_dir = tmp_path / 'src' / 'st_data_pkg'
    pkg_dir.mkdir(parents=True)

    (pkg_dir / 'pyproject.toml').write_text("""
[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"

[project]
name = "st_data_pkg"
version = "0.1.0"
description = "Setuptools data files and script override test"
dependencies = []

[project.scripts]
st-node = "st_data_pkg.cli:main"

[tool.setuptools]
packages = ["st_data_pkg"]

[tool.setuptools.data-files]
"share/st_data_pkg/launch" = ["launch/*.py"]
""")
    mod_dir = pkg_dir / 'st_data_pkg'
    mod_dir.mkdir()
    (mod_dir / '__init__.py').write_text('__version__ = "0.1.0"\n')
    (mod_dir / 'cli.py').write_text('def main():\n    print("ST_NODE_OK")\n')

    launch_dir = pkg_dir / 'launch'
    launch_dir.mkdir()
    launch_file = launch_dir / 'app.py'
    launch_file.write_text('APP = 1\n')

    desc = PackageDescriptor(pkg_dir)
    PEP621PackageIdentification().identify(desc)

    build_base = tmp_path / 'build'
    install_base = tmp_path / 'install'
    build_base.mkdir(parents=True)
    install_base.mkdir(parents=True)

    task = PythonProjectBuildTask()
    context = TaskContext(
        pkg=desc,
        args=SimpleNamespace(
            path=str(pkg_dir),
            build_base=str(build_base),
            install_base=str(install_base),
            symlink_install=True,
        ),
        dependencies={},
    )
    task.set_context(context=context)
    res = run_until_complete(
        task.build(script_dir_override='lib/st_data_pkg')
    )
    assert res is None

    # Script installed under overridden lib/<pkg> rather than bin/ or Scripts/
    script_dir = install_base / 'lib' / 'st_data_pkg'
    assert any(script_dir.glob('st-node*'))
    assert not (install_base / 'bin' / 'st-node').exists()
    assert not (install_base / 'Scripts' / 'st-node.exe').exists()

    # Data file linked even though setuptools build_editable omits .data/
    installed_launch = (
        install_base / 'share' / 'st_data_pkg' / 'launch' / 'app.py'
    )
    assert installed_launch.is_symlink()
    assert os.path.samefile(installed_launch, launch_file)


def test_integration_python_project_test_task(tmp_path):
    """Test end-to-end PythonProjectBuildTask + PythonProjectTestTask."""
    pytest.importorskip('flit_core')

    pkg_dir = tmp_path / 'src' / 'tested_pkg'
    pkg_dir.mkdir(parents=True)

    (pkg_dir / 'pyproject.toml').write_text(
        '[build-system]\n'
        'requires = ["flit_core >=3.2,<4"]\n'
        'build-backend = "flit_core.buildapi"\n'
        '\n'
        '[project]\n'
        'name = "tested_pkg"\n'
        'version = "0.1.0"\n'
        'description = "Testable PEP 621 package"\n'
        '\n'
        '[dependency-groups]\n'
        'test = ["pytest"]\n',
        encoding='utf-8',
    )
    mod_dir = pkg_dir / 'tested_pkg'
    mod_dir.mkdir()
    (mod_dir / '__init__.py').write_text(
        '"""Tested package."""\n'
        'def add(a, b):\n'
        '    return a + b\n',
        encoding='utf-8',
    )
    tests_dir = pkg_dir / 'tests'
    tests_dir.mkdir()
    (tests_dir / 'test_add.py').write_text(
        'from tested_pkg import add\n\n\n'
        'def test_add():\n'
        '    assert add(2, 3) == 5\n',
        encoding='utf-8',
    )

    desc = PackageDescriptor(pkg_dir)
    PEP621PackageIdentification().identify(desc)
    PEP621PackageAugmentation().augment_package(desc)

    build_base = tmp_path / 'build' / 'tested_pkg'
    install_base = tmp_path / 'install'
    test_result_base = tmp_path / 'test_results' / 'tested_pkg'
    build_base.mkdir(parents=True)
    install_base.mkdir(parents=True)

    build_task = PythonProjectBuildTask()
    build_task.set_context(
        context=TaskContext(
            pkg=desc,
            args=SimpleNamespace(
                path=str(pkg_dir),
                build_base=str(build_base),
                install_base=str(install_base),
                symlink_install=False,
            ),
            dependencies={},
        )
    )
    assert run_until_complete(build_task.build()) is None

    events = []
    test_context = TaskContext(
        pkg=desc,
        args=SimpleNamespace(
            path=str(pkg_dir),
            build_base=str(build_base),
            install_base=str(install_base),
            test_result_base=str(test_result_base),
            python_testing=None,
            pytest_args=None,
            pytest_with_coverage=False,
            retest_until_fail=0,
            retest_until_pass=0,
        ),
        dependencies={desc.name: str(install_base)},
    )
    test_context.put_event_into_queue = events.append

    test_task = PythonProjectTestTask()
    test_task.set_context(context=test_context)
    rc = run_until_complete(test_task.test())
    assert not rc
    assert (test_result_base / 'pytest.xml').is_file()


def test_integration_dynamic_metadata_flit_core(tmp_path):
    """Test real PEP 517 dynamic version resolution via flit_core."""
    pytest.importorskip('flit_core')

    pkg_dir = tmp_path / 'src' / 'dynamic_flit_pkg'
    pkg_dir.mkdir(parents=True)
    (pkg_dir / 'pyproject.toml').write_text(
        '[build-system]\n'
        'requires = ["flit_core >=3.2,<4"]\n'
        'build-backend = "flit_core.buildapi"\n'
        '\n'
        '[project]\n'
        'name = "dynamic_flit_pkg"\n'
        'dynamic = ["version", "description"]\n',
        encoding='utf-8',
    )
    mod_dir = pkg_dir / 'dynamic_flit_pkg'
    mod_dir.mkdir()
    (mod_dir / '__init__.py').write_text(
        '"""Dynamic flit package."""\n'
        '__version__ = "3.4.5"\n',
        encoding='utf-8',
    )

    desc = PackageDescriptor(pkg_dir)
    PEP621PackageIdentification().identify(desc)
    assert desc.type == 'python.project'

    PEP621PackageAugmentation().augment_package(desc)
    assert desc.metadata.get('version') == '3.4.5'
    assert 'get_python_project_metadata' in desc.metadata
