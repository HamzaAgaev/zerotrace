"""Compatibility for consumers of the original flat backend packages."""

import sys
from importlib import import_module


def publish_legacy_package(name: str, submodules: list[str]) -> None:
    """Preserve the flat import paths published by the first backend release."""
    if name.startswith("backend."):
        return
    qualified = f"backend.{name}"
    package = import_module(qualified)
    sys.modules[name] = package
    for child in submodules:
        sys.modules[f"{name}.{child}"] = import_module(f"{qualified}.{child}")
