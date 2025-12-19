"""SARIF log builder for Concordat Auditor."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import pathlib
import typing as typ

if typ.TYPE_CHECKING:
    from .models import CheckDefinition, Finding


class SarifBuilder:
    """Helper for constructing SARIF 2.1.0 payloads."""

    def __init__(
        self,
        *,
        tool_name: str,
        tool_version: str = "0.1.0",
        information_uri: str | None = None,
    ) -> None:
        """Store metadata for the SARIF run."""
        self.tool_name = tool_name
        self.tool_version = tool_version
        self.information_uri = information_uri or (
            "https://github.com/leynos/concordat"
        )
        self._rules: dict[str, CheckDefinition] = {}
        self._results: list[dict[str, object]] = []

    def register_rules(self, rules: typ.Iterable[CheckDefinition]) -> None:
        """Register rules prior to emitting findings."""
        for rule in rules:
            self._rules[rule.rule_id] = rule

    def add_findings(
        self,
        findings: typ.Sequence[Finding],
        *,
        resource_fallback: str,
    ) -> None:
        """Add findings to the SARIF result list."""
        for finding in findings:
            fingerprint_source = f"{finding.rule_id}-{finding.message}"
            fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()
            location_uri = finding.resource or resource_fallback
            serialized = {
                "ruleId": finding.rule_id,
                "level": finding.level,
                "message": {"text": finding.message},
                "locations": [
                    {"physicalLocation": {"artifactLocation": {"uri": location_uri}}}
                ],
                "partialFingerprints": {"findingId": fingerprint},
            }
            if finding.properties:
                serialized["properties"] = finding.properties
            self._results.append(serialized)

    def build(self) -> dict[str, object]:
        """Return the SARIF document."""
        run = {
            "tool": {
                "driver": {
                    "name": self.tool_name,
                    "version": self.tool_version,
                    "informationUri": self.information_uri,
                    "rules": [
                        self._serialize_rule(rule) for rule in self._rules.values()
                    ],
                }
            },
            "results": self._results,
            "invocations": [
                {
                    "executionSuccessful": True,
                    "endTimeUtc": dt.datetime.now(tz=dt.UTC).isoformat(),
                }
            ],
        }
        return {
            "version": "2.1.0",
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [run],
        }

    def write(self, path: pathlib.Path) -> pathlib.Path:
        """Persist the SARIF log to disk."""
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        document = self.build()
        path.write_text(json.dumps(document, indent=2))
        return path

    def _serialize_rule(self, rule: CheckDefinition) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": rule.rule_id,
            "name": rule.name,
            "shortDescription": {"text": rule.short_description},
            "fullDescription": {"text": rule.long_description},
            "defaultConfiguration": {"level": rule.level},
        }
        if rule.help_uri:
            payload["helpUri"] = rule.help_uri
        payload["properties"] = {"ruleId": rule.rule_id}
        return payload
