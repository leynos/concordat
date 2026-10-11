"""Build the `policy-input/yaml-lint-baseline` envelope.

The YAML lint rule package reasons over five kinds of fact: the root
`Makefile` (parsed by the pinned `makeutil`, as the Markdown and spelling
envelopes do), the estate yamllint base file (as a digest, because the
baseline requires it byte for byte), the repository's own yamllint
configuration, the actionlint configuration, and every GitHub Actions workflow
under `.github/workflows`. Each fact is recorded as it was found: a file that
exists but cannot be decoded is carried with its `error`, so the policy
reports an indeterminate verdict rather than reading the file as absent.

The file readers are `markdown_envelope`'s, so every envelope shares one
containment guard: no policy input is read from outside the checkout.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import typing as typ

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from concordat.errors import OperationalRuleError

from .makefile_facts import MakefileInspector, inspect_makefile
from .markdown_envelope import (
    OPERATION_PROBE_PATH,
    OPERATION_READ_MAKEFILE,
    PRUNED_DIRECTORIES,
    Repository,
    WorkflowFile,
    is_file,
    is_symlink,
    json_safe,
    load_workflows,
    raise_walk_error,
    read_text,
    resolved_root,
    within_checkout,
)

if typ.TYPE_CHECKING:
    from .makefile_facts import MakeutilReport

ENVELOPE_SCHEMA_VERSION: typ.Final = 1
ENVELOPE_KIND: typ.Final = "policy-input/yaml-lint-baseline"
YAMLLINT_BASE_FILENAME: typ.Final = ".yamllint-estate.yml"
YAMLLINT_LOCAL_FILENAME: typ.Final = ".yamllint.yml"
# The other project-level names yamllint reads, and reads before
# `.yamllint.yml`, so each silently replaces the file the baseline governs. They
# are reported as facts for the policy to name in its finding. The user-global
# `$XDG_CONFIG_HOME/yamllint/config` is not a repository file and is not looked
# for: yamllint consults it only when no project file exists.
ALTERNATE_YAMLLINT_FILENAMES: typ.Final = (".yamllint", ".yamllint.yaml")
# actionlint reads either spelling, `.yaml` first.
ACTIONLINT_CONFIG_FILENAMES: typ.Final = (
    ".github/actionlint.yaml",
    ".github/actionlint.yml",
)
YAML_SUFFIXES: typ.Final = frozenset({".yml", ".yaml"})

OPERATION_READ_YAMLLINT_BASE: typ.Final = "read-yamllint-base"
OPERATION_READ_YAMLLINT_LOCAL: typ.Final = "read-yamllint-local"
OPERATION_READ_ACTIONLINT_CONFIG: typ.Final = "read-actionlint-config"
OPERATION_SCAN_YAML: typ.Final = "scan-yaml-files"


class DecodedFile(typ.TypedDict):
    """One decoded YAML input file, or the file with the reason it failed."""

    path: str
    parsed: object | None
    error: str | None


class DigestedFile(typ.TypedDict):
    """One input file recorded by its SHA-256, or carrying its read error."""

    path: str
    sha256: str | None
    error: str | None


class YamlLintApplicability(typ.TypedDict):
    """Evidence that the YAML lint rule package applies to the checkout."""

    yaml_files: bool
    workflows: bool
    root_makefile: bool


class YamlLintEnvelope(typ.TypedDict):
    """The `policy-input/yaml-lint-baseline` document."""

    schema_version: int
    kind: str
    repository: Repository
    applicability: YamlLintApplicability
    makefile: MakeutilReport | None
    yamllint_base: DigestedFile | None
    yamllint_local: DecodedFile | None
    alternate_yamllint_configs: list[str]
    actionlint_config: DecodedFile | None
    workflows: list[WorkflowFile]


def _is_tracked_yaml(candidate: pathlib.Path) -> bool:
    """Return whether *candidate* is a regular YAML file rather than a link.

    The check never follows symbolic links, so a link alone does not count.

    Returns
    -------
    bool
        Whether the path has a YAML suffix and is a regular file.
    """
    if candidate.suffix.lower() not in YAML_SUFFIXES:
        return False
    return not is_symlink(candidate, OPERATION_SCAN_YAML) and is_file(
        candidate, OPERATION_SCAN_YAML
    )


def _has_yaml_files(checkout: pathlib.Path) -> bool:
    """Return whether any YAML file exists outside the pruned directories.

    A directory that cannot be listed raises rather than being skipped,
    through `markdown_envelope`'s walk-error handler.

    Returns
    -------
    bool
        Whether a YAML file was found.
    """
    for current, directories, files in os.walk(checkout, onerror=raise_walk_error):
        directories[:] = sorted(
            name for name in directories if name not in PRUNED_DIRECTORIES
        )
        if any(_is_tracked_yaml(pathlib.Path(current) / name) for name in files):
            return True
    return False


def _read_input(
    checkout: pathlib.Path, root: pathlib.Path, name: str, operation: str
) -> tuple[str | None, str | None] | None:
    """Return one input file's text or its decoding error, or None when absent.

    An `OperationalRuleError` propagates from the containment guard, or when
    the file exists but cannot be opened.

    Returns
    -------
    tuple[str | None, str | None] | None
        ``(text, None)``, ``(None, error)`` for undecodable bytes, or ``None``
        when the file does not exist.
    """
    path = checkout / name
    if not within_checkout(root, path, operation) or not is_file(path, operation):
        return None
    try:
        return read_text(path, operation), None
    except UnicodeDecodeError as error:
        return None, f"not UTF-8 text: {error}"


def _decode_yaml(text: str) -> tuple[object | None, str | None]:
    """Decode YAML text into a JSON-safe document, or the reason it failed.

    A parser is created per call: ruamel.yaml does not promise that an
    instance stays usable after a failed load, and a bad configuration must
    not corrupt the reading of the next one.

    Returns
    -------
    tuple[object | None, str | None]
        ``(document, None)`` on success, or ``(None, reason)``. A mapping key
        JSON cannot carry, such as a YAML date, is a reason, never a crash.
    """
    try:
        return json_safe(YAML(typ="safe").load(text)), None
    except YAMLError as decode_error:
        return None, f"invalid YAML: {decode_error}"
    except TypeError as key_error:
        return None, f"unsupported mapping key: {key_error}"


def _load_yaml_file(
    checkout: pathlib.Path, root: pathlib.Path, name: str, operation: str
) -> DecodedFile | None:
    """Return the decoded YAML file, or ``None`` when it is absent.

    Returns
    -------
    DecodedFile | None
        The file decoded as YAML, or carrying its decoding error.
    """
    read = _read_input(checkout, root, name, operation)
    if read is None:
        return None
    text, error = read
    if text is None:
        return {"path": name, "parsed": None, "error": error}
    parsed, reason = _decode_yaml(text)
    return {"path": name, "parsed": parsed, "error": reason}


def _load_base(checkout: pathlib.Path, root: pathlib.Path) -> DigestedFile | None:
    """Return the estate yamllint base file's digest, or ``None`` when absent.

    The digest is taken over the file's bytes, so a copy that differs only in
    whitespace is a different file: the baseline asks for the canonical file
    verbatim.

    Returns
    -------
    DigestedFile | None
        The file's SHA-256, or ``None`` when the file does not exist.

    Raises
    ------
    OperationalRuleError
        If the file exists but cannot be read.
    """
    path = checkout / YAMLLINT_BASE_FILENAME
    operation = OPERATION_READ_YAMLLINT_BASE
    if not within_checkout(root, path, operation) or not is_file(path, operation):
        return None
    # Hashed from the bytes rather than from decoded text, so an undecodable
    # copy still has a digest and is reported as a mismatch.
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        message = f"cannot read {path}: {error}"
        raise OperationalRuleError(
            message, operation=operation, resource=path
        ) from error
    return {"path": YAMLLINT_BASE_FILENAME, "sha256": digest, "error": None}


def _alternate_yamllint_configs(checkout: pathlib.Path) -> list[str]:
    """Return the other yamllint configuration file names present.

    Returns
    -------
    list[str]
        The alternate configuration file names the checkout carries.
    """
    return [
        name
        for name in ALTERNATE_YAMLLINT_FILENAMES
        if is_file(checkout / name, OPERATION_PROBE_PATH)
    ]


def _load_actionlint_config(
    checkout: pathlib.Path, root: pathlib.Path
) -> DecodedFile | None:
    """Return the decoded actionlint configuration, or ``None`` when absent.

    Returns
    -------
    DecodedFile | None
        The first of the two accepted spellings that exists, decoded or
        carrying its decoding error.
    """
    for name in ACTIONLINT_CONFIG_FILENAMES:
        fact = _load_yaml_file(checkout, root, name, OPERATION_READ_ACTIONLINT_CONFIG)
        if fact is not None:
            return fact
    return None


def build_yaml_lint_envelope(
    checkout: pathlib.Path,
    *,
    inspect: MakefileInspector = inspect_makefile,
) -> YamlLintEnvelope:
    """Assemble the YAML lint policy input for one local checkout.

    Parameters
    ----------
    checkout:
        Path to the checkout under audit.
    inspect:
        Reads the root Makefile's facts. The default is the pure query; the
        command boundary injects an observing one.

    An `OperationalRuleError` propagates from the fact readers if the root
    `Makefile` cannot be parsed by `makeutil`, a fact file exists but cannot
    be opened, or a fact file resolves to a location outside the checkout.

    Returns
    -------
    YamlLintEnvelope
        The policy input document assembled from the checkout.
    """
    root = resolved_root(checkout)
    makefile_path = checkout / "Makefile"
    makefile_report: MakeutilReport | None = None
    if within_checkout(root, makefile_path, OPERATION_READ_MAKEFILE) and is_file(
        makefile_path, OPERATION_READ_MAKEFILE
    ):
        makefile_report = inspect(makefile_path).report
    workflows = load_workflows(checkout, root)
    return {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "kind": ENVELOPE_KIND,
        "repository": {"path": str(checkout), "name": None},
        "applicability": {
            "yaml_files": _has_yaml_files(checkout),
            "workflows": len(workflows) > 0,
            "root_makefile": makefile_report is not None,
        },
        "makefile": makefile_report,
        "yamllint_base": _load_base(checkout, root),
        "yamllint_local": _load_yaml_file(
            checkout, root, YAMLLINT_LOCAL_FILENAME, OPERATION_READ_YAMLLINT_LOCAL
        ),
        "alternate_yamllint_configs": _alternate_yamllint_configs(checkout),
        "actionlint_config": _load_actionlint_config(checkout, root),
        "workflows": workflows,
    }
