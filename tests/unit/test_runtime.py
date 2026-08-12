"""Tests for the optional native runtime boundary."""

from __future__ import annotations

import importlib
import types
import typing as typ

from concordat import pure, runtime

if typ.TYPE_CHECKING:
    import pytest


def test_runtime_uses_native_hello_when_extension_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime selects the optional extension's implementation."""

    def native_hello() -> str:
        return "hello from Rust"

    native_module = types.SimpleNamespace(hello=native_hello)
    monkeypatch.setattr(runtime.importlib, "import_module", lambda _: native_module)

    reloaded_runtime = importlib.reload(runtime)

    assert reloaded_runtime.hello is native_hello


def test_runtime_falls_back_to_pure_hello_when_extension_is_missing() -> None:
    """The runtime retains a working Python implementation without the extension."""
    reloaded_runtime = importlib.reload(runtime)

    assert reloaded_runtime.hello is pure.hello
