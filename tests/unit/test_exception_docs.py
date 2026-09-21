"""Unit tests for the recorded codegen-backend exception scan.

The scan is the only part of the build-defaults rule that reads prose, so its
boundaries matter: a heading inside a fenced code block is not a section, a
subsection belongs to its parent, and the channel spellings recovered from a
body are the evidence the policy compares against the pinned toolchain.
"""

from __future__ import annotations

import typing as typ

from concordat.rules.exception_docs import extract_headings, find_exception_sections

if typ.TYPE_CHECKING:
    import pathlib

GUIDE: typ.Final = "docs/developers-guide.md"

NETSUKE_SHAPED_GUIDE: typ.Final = """# Developers' guide

## The build standard

The mold linker and the parallel frontend are the defaults.

### Why Cranelift is not part of the standard

Measured on 2026-09-18 on `nightly-2026-08-23`, a Cranelift-compiled panic
does not find the unwind handler it should.

```rust
/// A panic raised on the main thread, caught by `catch_unwind`.
#[test]
fn main_thread_catch_unwind() {
    let caught = std::panic::catch_unwind(|| panic!("boom"));
    assert!(caught.is_err());
}
```

#### What was ruled out

Not the linker, and not the parallel frontend.

## Coverage

Coverage runs on the LLVM backend.
"""


def write_guide(root: pathlib.Path, body: str) -> None:
    """Write *body* as the checkout's developers' guide."""
    guide = root / GUIDE
    guide.parent.mkdir(parents=True, exist_ok=True)
    guide.write_text(body, encoding="utf-8")


class TestHeadingExtraction:
    """Headings are document structure; heading-shaped content is not."""

    def test_a_heading_inside_a_fence_is_not_a_heading(self) -> None:
        """Every one of these sections quotes code, and Rust comments start `#`."""
        lines = [
            "## Real heading",
            "```text",
            "## Not a heading",
            "```",
            "## Second real heading",
        ]
        found = [(heading.level, heading.text) for heading in extract_headings(lines)]
        assert found == [(2, "Real heading"), (2, "Second real heading")]

    def test_a_shorter_run_does_not_close_a_longer_fence(self) -> None:
        """A four-backtick fence is closed only by four or more backticks."""
        lines = ["````text", "```", "## Still inside", "````", "## Outside"]
        found = [heading.text for heading in extract_headings(lines)]
        assert found == ["Outside"]

    def test_closing_hashes_are_stripped(self) -> None:
        """The closed ATX form names the same section as the open one."""
        found = [heading.text for heading in extract_headings(["### Cranelift ###"])]
        assert found == ["Cranelift"]


class TestExceptionSections:
    """A section naming the backend is the repository's recorded exception."""

    def test_the_netsuke_shaped_guide_records_an_exception(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The reference repository's current state must be recognised."""
        write_guide(tmp_path, NETSUKE_SHAPED_GUIDE)
        scans = find_exception_sections(tmp_path, [GUIDE], "Cranelift")
        assert len(scans) == 1
        sections = scans[0]["sections"]
        assert len(sections) == 1
        assert sections[0]["heading"] == "Why Cranelift is not part of the standard"
        assert "nightly-2026-08-23" in sections[0]["channels_named"]

    def test_a_subsection_belongs_to_the_section_that_owns_it(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The body runs to the next peer heading, not to the next heading."""
        write_guide(tmp_path, NETSUKE_SHAPED_GUIDE)
        scans = find_exception_sections(tmp_path, [GUIDE], "Cranelift")
        body = scans[0]["sections"][0]["body_lines"]
        # The prose, the fenced example, and the nested subsection, but not the
        # sibling "Coverage" section that follows at a shallower level.
        assert body >= 10
        assert "Coverage runs on the LLVM backend." not in str(
            scans[0]["sections"][0]["channels_named"]
        )

    def test_a_guide_without_the_keyword_records_no_exception(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Absence of a section is the state the policy refuses, so it must read so."""
        write_guide(tmp_path, "# Guide\n\n## The build standard\n\nmold and threads.\n")
        scans = find_exception_sections(tmp_path, [GUIDE], "Cranelift")
        assert scans[0]["present"] is True
        assert scans[0]["sections"] == []

    def test_a_missing_document_is_absent_not_unreadable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A repository with no guide has no exception and no read failure."""
        scans = find_exception_sections(tmp_path, [GUIDE], "Cranelift")
        assert scans[0] == {
            "path": GUIDE,
            "present": False,
            "read_error": None,
            "sections": [],
        }

    def test_the_keyword_match_ignores_case(self, tmp_path: pathlib.Path) -> None:
        """A guide writing the backend in lower case still records the exception."""
        write_guide(tmp_path, "# Guide\n\n## why cranelift is out\n\nOn `stable`.\n")
        scans = find_exception_sections(tmp_path, [GUIDE], "Cranelift")
        assert len(scans[0]["sections"]) == 1

    def test_only_channel_spellings_are_collected(self, tmp_path: pathlib.Path) -> None:
        """A shared-object version in the evidence is not a toolchain channel."""
        write_guide(
            tmp_path,
            "# Guide\n\n## Cranelift\n\n"
            "Measured on `nightly-2026-08-23`, whose backend is\n"
            "`librustc_codegen_cranelift-1.100.0-nightly.so`.\n",
        )
        scans = find_exception_sections(tmp_path, [GUIDE], "Cranelift")
        named = scans[0]["sections"][0]["channels_named"]
        assert "nightly-2026-08-23" in named
        assert "librustc_codegen_cranelift-1.100.0-nightly.so" not in named

    def test_every_declared_document_is_scanned_in_order(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A repository may record the exception in a document of its choosing."""
        write_guide(tmp_path, "# Guide\n\n## Nothing here\n")
        adr = tmp_path / "docs" / "adr-029.md"
        adr.write_text("# ADR\n\n## Cranelift\n\nOn `nightly`.\n", encoding="utf-8")
        scans = find_exception_sections(
            tmp_path, [GUIDE, "docs/adr-029.md"], "Cranelift"
        )
        assert [scan["path"] for scan in scans] == [GUIDE, "docs/adr-029.md"]
        assert scans[0]["sections"] == []
        assert len(scans[1]["sections"]) == 1
