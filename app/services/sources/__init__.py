"""Auto-discovery for source marketplaces. Each subdirectory that exposes a
`Source` class in its __init__.py is automatically registered."""

import importlib
import pkgutil
from pathlib import Path

from app.services.sources.source import MarketplaceSource


def get_sources() -> list[MarketplaceSource]:
    sources = []
    package_dir = Path(__file__).parent
    for finder, name, is_pkg in pkgutil.iter_modules([str(package_dir)]):
        if not is_pkg:
            continue
        module = importlib.import_module(f"app.services.sources.{name}")
        if hasattr(module, "Source"):
            sources.append(module.Source())
    return sources


def get_source_for_url(url: str) -> MarketplaceSource | None:
    for source in get_sources():
        if source.name.lower() in url.lower():
            return source
    return None
