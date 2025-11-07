"""Priority label contract loading."""

from __future__ import annotations

import dataclasses as dc
import pathlib
import typing as typ

from ruamel.yaml import YAML

yaml = YAML(typ="safe")


@dc.dataclass(frozen=True)
class PriorityLabel:
    """Single canonical label entry."""

    key: str
    name: str
    color: str
    description: str


@dc.dataclass(frozen=True)
class PriorityFieldOption:
    """Projects v2 field option derived from the canonical model."""

    key: str
    display_name: str


@dc.dataclass(frozen=True)
class PriorityField:
    """Projects v2 field contract."""

    name: str
    type: str
    options: tuple[PriorityFieldOption, ...]


@dc.dataclass(frozen=True)
class PriorityModel:
    """Top-level model consumed by the Auditor."""

    schema_version: int
    labels: tuple[PriorityLabel, ...]
    field: PriorityField | None = None
    aliases: tuple[tuple[str, str], ...] = ()


_DEFAULT_LABELS: tuple[PriorityLabel, ...] = (
    PriorityLabel(
        key="P0",
        name="priority/p0-blocker",
        color="b60205",
        description="Blocking incidents or outages that require immediate action.",
    ),
    PriorityLabel(
        key="P1",
        name="priority/p1-critical",
        color="d93f0b",
        description="Critical work that unblocks delivery within the current sprint.",
    ),
    PriorityLabel(
        key="P2",
        name="priority/p2-major",
        color="fbca04",
        description="Important enhancements that should be planned this quarter.",
    ),
    PriorityLabel(
        key="P3",
        name="priority/p3-normal",
        color="0e8a16",
        description="Triage queue for standard work with flexible scheduling.",
    ),
)

_DEFAULT_FIELD = PriorityField(
    name="Priority",
    type="single_select",
    options=tuple(
        PriorityFieldOption(key=label.key, display_name=label.key)
        for label in _DEFAULT_LABELS
    ),
)

_DEFAULT_MODEL = PriorityModel(
    schema_version=1,
    labels=_DEFAULT_LABELS,
    field=_DEFAULT_FIELD,
)


def _sorted_labels(labels: typ.Iterable[PriorityLabel]) -> tuple[PriorityLabel, ...]:
    return tuple(sorted(labels, key=lambda label: label.key))


def load_priority_model(path: pathlib.Path | None) -> PriorityModel:
    """Load the canonical priority model, falling back to defaults."""
    if not path:
        return _DEFAULT_MODEL
    target = pathlib.Path(path)
    if target.is_dir():
        target = target / "canon" / "priorities" / "priority-model.yaml"
    if not target.exists():
        return _DEFAULT_MODEL

    data = yaml.load(target.read_text())
    try:
        schema_version = int(data["schema_version"])
    except (KeyError, TypeError, ValueError) as error:
        message = "priority model missing schema_version"
        raise ValueError(message) from error

    labels_data = data.get("labels", [])
    labels = [
        PriorityLabel(
            key=str(entry["key"]),
            name=str(entry["name"]),
            color=str(entry["color"]),
            description=str(entry.get("description", "")).strip(),
        )
        for entry in labels_data
    ]

    field: PriorityField | None = None
    if "field" in data:
        field_entry = data["field"]
        options = tuple(
            PriorityFieldOption(
                key=str(option["key"]),
                display_name=str(option.get("name", option["key"])),
            )
            for option in field_entry.get("options", [])
        )
        field = PriorityField(
            name=str(field_entry.get("name", "Priority")),
            type=str(field_entry.get("type", "single_select")),
            options=options,
        )

    aliases_data = data.get("aliases", [])
    aliases: list[tuple[str, str]] = [
        (str(alias["from"]), str(alias["to"])) for alias in aliases_data
    ]

    resolved_labels = _sorted_labels(labels or _DEFAULT_LABELS)
    return PriorityModel(
        schema_version=schema_version,
        labels=resolved_labels,
        field=field or _DEFAULT_FIELD,
        aliases=tuple(sorted(aliases)),
    )
