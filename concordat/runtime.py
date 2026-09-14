"""Bind the public greeting to its native implementation when available.

``concordat.hello`` re-exports this module's ``hello`` binding. Concordat uses
``_concordat_rs.hello`` when the optional Rust extension is installed and falls
back to ``.pure.hello`` when the extension itself is absent. Import failures
from the extension's dependencies propagate to callers unchanged.
"""

import importlib
import typing as typ

type Hello = typ.Callable[[], str]

try:  # pragma: no cover - Rust optional
    hello = typ.cast("Hello", importlib.import_module("_concordat_rs").hello)
except ModuleNotFoundError as exc:  # pragma: no cover - Python fallback
    if exc.name != "_concordat_rs":
        raise
    from .pure import hello as hello
