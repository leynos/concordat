"""Select the optional Rust implementation when it is installed."""

import importlib
import typing as typ

type Hello = typ.Callable[[], str]

try:  # pragma: no cover - Rust optional
    hello = typ.cast("Hello", importlib.import_module("_concordat_rs").hello)
except ModuleNotFoundError as exc:  # pragma: no cover - Python fallback
    if exc.name != "_concordat_rs":
        raise
    from .pure import hello as hello
