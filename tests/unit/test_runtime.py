"""Tests for the optional native runtime boundary."""

from __future__ import annotations

import importlib
import types
import typing as typ

import pytest

import concordat
from concordat import pure, runtime


def test_runtime_uses_native_hello_when_extension_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime selects the optional extension's implementation."""

    def native_hello() -> str:
        return "hello from Rust"

    native_module = types.SimpleNamespace(hello=native_hello)
    try:
        with monkeypatch.context() as context:
            context.setattr(runtime.importlib, "import_module", lambda _: native_module)
            reloaded_runtime = importlib.reload(runtime)
            reloaded_concordat = importlib.reload(concordat)

            assert reloaded_runtime.hello is native_hello
            assert reloaded_concordat.hello is native_hello
    finally:
        importlib.reload(runtime)
        importlib.reload(concordat)


def test_runtime_falls_back_to_pure_hello_when_extension_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime retains a working Python implementation without the extension."""

    def raise_module_not_found(_: str) -> typ.NoReturn:
        raise ModuleNotFoundError(name="_concordat_rs")

    try:
        with monkeypatch.context() as context:
            context.setattr(runtime.importlib, "import_module", raise_module_not_found)
            reloaded_runtime = importlib.reload(runtime)
            reloaded_concordat = importlib.reload(concordat)

            assert reloaded_runtime.hello is pure.hello
            assert reloaded_concordat.hello is pure.hello
    finally:
        importlib.reload(runtime)
        importlib.reload(concordat)


def test_runtime_reraises_missing_native_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native extension dependency failure must remain visible to callers."""
    error = ModuleNotFoundError(name="native_dependency")

    def raise_dependency_error(_: str) -> typ.NoReturn:
        raise error

    try:
        with monkeypatch.context() as context:
            context.setattr(runtime.importlib, "import_module", raise_dependency_error)

            with pytest.raises(ModuleNotFoundError) as raised:
                importlib.reload(runtime)

            assert raised.value is error
    finally:
        importlib.reload(runtime)
        importlib.reload(concordat)
