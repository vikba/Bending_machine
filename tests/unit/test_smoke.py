"""Smoke test: every module of the package imports."""

import importlib
import pkgutil

import bending_machine


def _module_names() -> list[str]:
    names = [bending_machine.__name__]
    for info in pkgutil.walk_packages(bending_machine.__path__, prefix="bending_machine."):
        names.append(info.name)
    return sorted(names)


def test_every_module_imports() -> None:
    names = _module_names()
    # 9 modules plus the package and __main__; guards against an empty walk.
    assert len(names) >= 11
    for name in names:
        importlib.import_module(name)
