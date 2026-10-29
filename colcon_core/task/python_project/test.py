# Copyright 2022-2026 Open Source Robotics Foundation, Inc.
# Licensed under the Apache License, Version 2.0

"""Task for testing python.project packages."""

from colcon_core.task.python.test import PythonTestTask


class PythonProjectTestTask(PythonTestTask):
    """Test Python project packages."""

    def add_arguments(self, *, parser):  # noqa: D102
        pass
