"""Read proof code from Rust sources with a parser (PT-006 facts)."""

from __future__ import annotations

import typing as typ

import pytest

from concordat.rules.prover_proof_facts import scan_proof_code

if typ.TYPE_CHECKING:
    import pathlib


def put(root: pathlib.Path, relative: str, text: str) -> None:
    """Write *text* to *relative* under *root*, creating directories."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def kinds(root: pathlib.Path) -> set[tuple[str, str]]:
    """Return the `(path, kind)` pairs the scan finds in a checkout."""
    return {(c["path"], c["kind"]) for c in scan_proof_code(root).proof_code}


@pytest.mark.parametrize(
    "source",
    [
        "#[kani::proof]\nfn h() {}\n",
        "#[kani::proof_for_contract(f)]\nfn h() {}\n",
        "use kani::proof;\n#[proof]\nfn h() {}\n",
        "use kani::proof as prove;\n#[prove]\nfn h() {}\n",
        "use kani::{any, proof};\n#[proof]\nfn h() {}\n",
        "use kani::*;\n#[proof]\nfn h() {}\n",
        "use kani as k;\n#[k::proof]\nfn h() {}\n",
        "#[cfg_attr(kani, kani::proof)]\nfn h() {}\n",
    ],
)
def test_a_kani_harness_is_found_however_it_is_spelled(
    tmp_path: pathlib.Path, source: str
) -> None:
    """Attributes resolve through the file's `use` declarations."""
    put(tmp_path, "src/kani.rs", source)
    assert kinds(tmp_path) == {("src/kani.rs", "kani-harness")}


@pytest.mark.parametrize(
    "source",
    [
        "// #[kani::proof]\nfn h() {}\n",
        "/// ```\n/// #[kani::proof]\n/// ```\nfn h() {}\n",
        'const S: &str = "#[kani::proof]";\n',
        "#[cfg(kani)]\nmod support { pub fn bound() {} }\n",
        "#[kani::unwind(3)]\nfn h() {}\n",
        "use other::proof;\n#[proof]\nfn h() {}\n",
        "#[proof]\nfn h() {}\n",
    ],
)
def test_text_that_only_resembles_a_harness_is_not_proof_code(
    tmp_path: pathlib.Path, source: str
) -> None:
    """Comments, strings, cfg helpers and unrelated attributes are not harnesses."""
    put(tmp_path, "src/lib.rs", source)
    assert kinds(tmp_path) == set()


@pytest.mark.parametrize(
    "source",
    [
        "verus! { fn f() {} }\n",
        "vstd::prelude::verus! { }\n",
        "use vstd::prelude::*;\n",
        "#[verifier::external_body]\nfn f() {}\n",
    ],
)
def test_verus_proof_code_is_found(tmp_path: pathlib.Path, source: str) -> None:
    """A `verus!` block, a vstd import or a verifier attribute is proof code."""
    put(tmp_path, "src/lib.rs", source)
    assert kinds(tmp_path) == {("src/lib.rs", "verus-block")}


def test_one_entry_is_kept_per_file_and_kind_at_the_first_line(
    tmp_path: pathlib.Path,
) -> None:
    """Fifty harnesses in one file are one fact, not fifty."""
    put(
        tmp_path,
        "src/kani.rs",
        "\n#[kani::proof]\nfn a() {}\n#[kani::proof]\nfn b() {}\n",
    )
    found = scan_proof_code(tmp_path).proof_code
    assert found == [{"path": "src/kani.rs", "kind": "kani-harness", "line": 2}]


GENERATOR = (
    "use quote::quote;\n"
    "pub fn emit() -> proc_macro2::TokenStream {\n"
    "    quote! { #[kani::proof] fn harness() {} }\n"
    "}\n"
)


def test_a_proc_macro_crate_that_emits_harnesses_holds_proof_code(
    tmp_path: pathlib.Path,
) -> None:
    """The generated harnesses are proof code that no ordinary build exercises."""
    put(tmp_path, "crates/macros/Cargo.toml", "[lib]\nproc-macro = true\n")
    put(tmp_path, "crates/macros/src/lib.rs", GENERATOR)
    assert kinds(tmp_path) == {("crates/macros/src/lib.rs", "proof-generator")}


def test_a_library_that_merely_quotes_a_harness_is_not_a_generator(
    tmp_path: pathlib.Path,
) -> None:
    """Outside a proc-macro crate, `quote!` text is data."""
    put(tmp_path, "crates/lib/Cargo.toml", '[lib]\nname = "lib"\n')
    put(tmp_path, "crates/lib/src/lib.rs", GENERATOR)
    assert kinds(tmp_path) == set()


@pytest.mark.parametrize(
    "directory", ["tests", "test", "fixtures", "examples", "target", "node_modules"]
)
def test_files_under_skipped_trees_are_not_scanned(
    tmp_path: pathlib.Path, directory: str
) -> None:
    """Fixtures and tests hold sample proofs, not the repository's own."""
    put(tmp_path, f"{directory}/kani.rs", "#[kani::proof]\nfn h() {}\n")
    assert kinds(tmp_path) == set()


def test_a_snapshot_is_not_rust_source(tmp_path: pathlib.Path) -> None:
    """Only `.rs` files are parsed."""
    put(tmp_path, "src/snapshots/out.snap", "#[kani::proof]\n")
    assert kinds(tmp_path) == set()


def test_a_syntax_error_that_names_a_prover_makes_the_file_unreadable(
    tmp_path: pathlib.Path,
) -> None:
    """An unparseable file may hide a harness, so it is reported, not skipped."""
    put(tmp_path, "src/kani.rs", "fn broken( { #[kani::proof] ]]]\n")
    scan = scan_proof_code(tmp_path)
    assert [u.split(":")[0] for u in scan.unreadable] == ["src/kani.rs"]
    assert scan.proof_code == []


def test_a_syntax_error_that_names_no_prover_is_ignored(tmp_path: pathlib.Path) -> None:
    """Syntax the parser predates cannot hide a proof, so it is not an error."""
    put(tmp_path, "src/lib.rs", "fn broken( { let x = ]]]\n")
    assert scan_proof_code(tmp_path).unreadable == []
