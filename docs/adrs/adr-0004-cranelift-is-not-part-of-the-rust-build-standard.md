# ADR-0004: Cranelift is not part of the Rust build standard

**Date:** 2026-11-14

**Status:** Accepted. Review on 14 May 2027.

## Context

The estate's Rust build standard, checked by the `rust-build-defaults` rule,
asked each repository to select the Cranelift codegen backend for the
development profile, or to record a current exception explaining why it did not
(BD-004, BD-006). The hope was faster edit-test loops.

On 13 and 14 November 2026 the estate measured that hope with interleaved runs,
on one build slot, with the backend confirmed in `cargo build -v`:

| Repository     | Speed                                          | Tests                                    | Target size            |
| -------------- | ---------------------------------------------- | ---------------------------------------- | ---------------------- |
| tei-rapporteur | about 10 to 15 percent faster edit-test loop   | not reported                             | 2 to 3 times larger    |
| gauss          | mixed: loop 28 percent faster, build 12 slower | 6 panic tests fail                       | 15.5 GB against 6.8 GB |
| podbot         | no gain (within noise)                         | lib test binary aborts on a panic        | 3.8 GB against 2.2 GB  |
| netsuke        | edit-test loop 11 percent slower               | 8 outcomes (7 tests) fail, all unwinding | 8.5 GB against 2.7 GB  |

The detail is in the bench reports of the SRE desk (stable bench and netsuke
bench). Load on the host was high throughout, so every timing is indicative.
Cranelift also does not unwind, lacks some SIMD intrinsics (zmij needs
`llvm.x86.sse2.pmulhu.w`, rustc_codegen_cranelift#171) and aborts on Windows
MSVC. CI never benefited, because coverage and release builds stay on LLVM.

## Decision

Larger builds would hurt us pretty badly. That is the deciding reason, in the
user's words: Cranelift target directories are two to three times the size
everywhere, and disk is tight. The speed evidence is secondary, and it is not
strong enough to outweigh the cost: no reliable wall-clock win, and several
suites that fail or abort.

LLVM is the default development backend. Cranelift is not part of the Rust
build standard for now. It is **disabled, not removed**:

- The rule no longer requires Cranelift or an exception. A new rule parameter,
  `cranelift_policy`, holds the stance as one value: `optional` (the default),
  `required` (the earlier standard) or `forbidden`.
- Under `optional`, BD-004 and BD-006 are inert. Cranelift is an allowed,
  unrecommended opt-in; BD-005, BD-007 and BD-008 still hold any repository
  that configures it to the safety shape (unstable gate, nightly pin, LLVM for
  coverage and release builds, Whitaker isolation).
- A per-package `codegen-backend = "llvm"` override is accepted under every
  value: it is support infrastructure for crates Cranelift cannot compile.
- Repositories comment out their Cranelift selection line, with a note naming
  this ADR and the review date, rather than deleting it. A commented-out key is
  not a selection.
- The support stays: the toolchain component, `cranelift: true` on
  install-whitaker, the install and check scripts, opt-in Make routes and
  fragments.

## Review

This is a deferral, not a rejection. The Rust community is moving towards
Cranelift for rapid rebuilds, and the landscape may look different in six
months. The decision is reviewed on **14 May 2027**, tracked in a concordat
issue.

A review must measure, by the netsuke method (interleaved runs, one build slot,
the backend confirmed in `cargo build -v` for every rustc invocation):

1. Build size first: target directories after build and after test, against
   LLVM. The review passes only if size is no longer worse. A speed gain alone
   does not reverse this decision.
2. The edit-test loop, cold build, clippy and test run, on netsuke, gauss and
   tei-rapporteur.
3. Whether panic-unwind tests pass (upstream: unwinding on by default,
   rustc_codegen_cranelift #1567 and #1584), the SIMD intrinsic gap (#171,
   #1712) and Windows behaviour.
4. The estate blockers: aws-lc, the genuine unwinding tests, and the zmij
   override that sits under weaver and lag-complexity.

If the review favours Cranelift, flip `cranelift_policy` to `required` (or leave
`optional`) and have repositories uncomment their selection.

## Consequences

Repositories may stop selecting Cranelift without recording an exception.
cuprum's `.concordat` exception waives BD-004 for a dev-fast fragment that
selects Cranelift; it stays valid and inert, and the estate row reverts the
fragment's selection by commenting it out.

The estate can measure the revert by running the rule with
`cranelift_policy: forbidden`; the default does not flag an opt-in.

See also
[ADR-0003](adr-0003-rust-build-defaults-reads-the-builds-that-replace-them.md).
