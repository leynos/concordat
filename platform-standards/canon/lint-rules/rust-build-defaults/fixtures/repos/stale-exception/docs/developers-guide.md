# Developers' guide

## The build standard

The linker and the parallel frontend are the defaults for every
development, test, lint and typecheck build.

### Why Cranelift is not part of the standard

Measured on `nightly-2026-03-26`, a Cranelift-compiled panic does not find the
unwind handler it should. `catch_unwind` fails to catch, and a panic
raised on a spawned thread aborts the process.

| Case                      | Cranelift          | LLVM control |
| ------------------------- | ------------------ | ------------ |
| `#[should_panic]`         | passes             | passes       |
| `catch_unwind`            | fails              | passes       |
| Panic on a spawned thread | aborts the process | passes       |

The failing tests are `main_thread_catch_unwind` and
`spawned_thread_panic_unwinds`. Re-measure on the next toolchain bump.
