"""Input collection helpers for persistence."""

from __future__ import annotations

import pytest

import concordat.persistence.inputs as persistence_inputs
from concordat import persistence


def test_non_interactive_missing_value_errors() -> None:
    """Non-interactive mode raises when required values are absent."""
    defaults = {
        "bucket": "",
        "region": "",
        "endpoint": "",
        "key_prefix": "",
        "key_suffix": "",
    }
    with pytest.raises(persistence.PersistenceError) as excinfo:
        persistence_inputs._collect_user_inputs(
            defaults,
            lambda _: "",
            preset={},
            allow_prompt=False,
        )

    assert "Bucket is required in non-interactive mode" in str(excinfo.value)
