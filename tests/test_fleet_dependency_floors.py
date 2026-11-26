"""Regression tests for dependency floors on the fleet's Linux CI target."""

import importlib
import tomllib
from pathlib import Path

from packaging.version import Version


def test_linux_resolution_preserves_an_inactive_windows_range(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    floors = importlib.import_module("fleet_dependency_floors")
    project = tmp_path / "pyproject.toml"
    project.write_text(
        """[project]
dependencies = [
    "demo>=3; sys_platform == 'linux'",
    "demo>=1,<2; sys_platform == 'win32'",
]
"""
    )
    monkeypatch.setattr(floors, "candidates", lambda req, floor: [Version("3")])
    monkeypatch.setattr(
        floors, "lowest_consistent", lambda reqs, floor: {"demo": Version("3")}
    )
    floors.raise_floors(project, (3, 13))
    deps = tomllib.loads(project.read_text())["project"]["dependencies"]
    assert deps[1] == "demo>=1,<2; sys_platform == 'win32'"


def test_aggregate_extra_does_not_raise_a_floor_on_the_project(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "tools"))
    floors = importlib.import_module("fleet_dependency_floors")
    project = tmp_path / "pyproject.toml"
    project.write_text(
        '[project]\nname = "demo-pkg"\nversion = "4.0"\n'
        '[project.optional-dependencies]\nall = ["Demo_Pkg[api]"]\napi = ["idna>=3"]\n'
    )
    monkeypatch.setattr(floors, "candidates", lambda req, floor: [Version("3")])
    monkeypatch.setattr(floors, "lowest_consistent", lambda reqs, floor: {})
    changes, _ = floors.raise_floors(project, (3, 13))
    assert changes == []
