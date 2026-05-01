"""Auto-discovery for supplier platforms. Each subdirectory that exposes a
`Platform` class in its __init__.py is automatically registered."""

import importlib
import pkgutil
from pathlib import Path

from app.services.platforms.platform import SupplierPlatform


def get_platforms() -> list[SupplierPlatform]:
    platforms = []
    package_dir = Path(__file__).parent
    for finder, name, is_pkg in pkgutil.iter_modules([str(package_dir)]):
        if not is_pkg:
            continue
        module = importlib.import_module(f"app.services.platforms.{name}")
        if hasattr(module, "Platform"):
            platforms.append(module.Platform())
    return platforms
