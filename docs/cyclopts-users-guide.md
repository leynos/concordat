# Cyclopts — a single‑file comprehensive guide

Last updated: 25 October 2025 (Europe/London)

This guide distils Cyclopts into one pragmatic, end‑to‑end document ready to
drop into a repo. It covers the core mental model, the API surface, design
patterns, testing, packaging, and a few sharp edges to avoid. All examples use
Python ≥3.10 and Bash.

______________________________________________________________________

## 0) Installation

```bash
python -m pip install cyclopts
# or for development against main
python -m pip install git+https://github.com/BrianPugh/cyclopts.git
```

> Cyclopts relies on Python type hints; annotate parameters deliberately.

______________________________________________________________________

## 1) Mental model in 60 seconds

- **`App`** holds command registry, defaults, help/version behaviour, and error
  policy.
- **Commands** are just Python callables registered with `@app.command` (or a
  nested `App`).
- **`@app.default`** is the action when no explicit command is given.
- **`run(func)`** is sugar for one‑function CLIs.
- **Parameters** are inferred from names, type hints, defaults, and docstrings;
  refine with `typing.Annotated[..., cyclopts.Parameter(...)]` when additional
  control is required.
- **Groups** organize help and enable **validators** across parameters.
- **Config providers** (TOML, env vars, in‑memory dicts) pre‑populate missing
  arguments.
- **Meta apps** wrap the primary app to inject session‑level options or alter
  invocation.
- **Result handling** is policy‑driven (`result_action`), so CLIs can either
  `sys.exit` or return values for tests/embedding.

______________________________________________________________________

## 2) Quick starts

### 2.1 Single‑function CLI

```python
import cyclopts

def greet(name: str, count: int = 1):
    for _ in range(count):
        print(f"Hello, {name}!")

if __name__ == "__main__":
    cyclopts.run(greet)
```

```bash
python app.py Alice --count 3
```

### 2.2 Multi‑command app

```python
from cyclopts import App

app = App(help="Demo multi‑command app")

@app.command
def fizz(n: int):
    print(f"FIZZ: {n}")

@app.command(alias="buzz")
def buzz_renamed(n: int):
    print(f"BUZZ: {n}")

@app.default
def main():
    print("Use a subcommand; try --help")

if __name__ == "__main__":
    app()
```

- Command names default to **hyphenated** function names; override with
  `name="..."` or change globally via `App.name_transform`.
- Register a **sub‑app** to create `parent sub ...` trees. Use `name="*"` to
  **flatten** a sub‑app’s commands into the parent namespace.

______________________________________________________________________

## 3) Parameters: the 95% used daily

### 3.1 Naming, aliases, shorts

```python
from typing import Annotated
from cyclopts import App, Parameter

app = App()

@app.command
def build(
    *,
    profile: Annotated[str, Parameter(name=["--profile", "-p"])],
    out_dir: Annotated[str, Parameter(name="--out-dir", alias=["-o"])],
):
    ...
```

- **Docstrings** should document the *Python variable names*, even if CLI names
  differ.
- Tune global behaviour with `App(default_parameter=Parameter(...))`.

### 3.2 Booleans & negation flags

Booleans are flags. `--foo` sets `True`. Cyclopts also provides negative forms
by default: `--no-foo`. Disable globally via
`default_parameter=Parameter(negative=())`, or customize per‑param with
`Parameter(negative="--anti-foo")`.

### 3.3 Counting flags (verbosity et al.)

```python
from typing import Annotated
from cyclopts import App, Parameter

app = App()

@app.default
def main(verbose: Annotated[int, Parameter(alias="-v", count=True)] = 0):
    print(f"verbosity={verbose}")
```

`-vvv` → `3`, `--verbose --verbose` → `2`.

### 3.4 Lists, tuples, dicts

- Lists consume tokens until an option is seen (positional) or can be repeated
  (keyword). Use `Parameter(consume_multiple=True)` to slurp tokens after a
  keyword.
- Use **dot notation** to build dicts: `--mapping.old new --mapping.foo bar` →
  `{"old": "new", "foo": "bar"}`. Prefer keyword‑only for dict params.
- Tuples coerce each element independently; fixed length is enforced.

### 3.5 Unions, Optionals, Literals, Enums, Flags

- **Union/Optional**: first match wins; `None` arms in a `Union` are ignored
  for coercion.
- **Literal**: define a constrained set of choices (numbers allowed) without
  writing manual validators.
- **Enum**: Cyclopts matches **by name** (case‑insensitive, hyphens tolerated
  for underscores). Prefer `Literal[...]` for user‑facing choices unless enum
  semantics are required.
- **Flag/IntFlag**: treat each member name as a boolean sub‑flag; can be set
  via positional names or `--param.member`.

### 3.6 Dates & times

- `date`: ISO `YYYY‑MM‑DD` (plus Python ≥3.11 ISO‑8601 variants).
- `datetime`: permissive ISO forms (`YYYY‑MM‑DD`,
  `YYYY‑MM‑DDTHH:MM:SS[.fff][±TZ]`).
- `timedelta`: compact units like `90m`, `1h30m`, `3w`, `6M`, `1y`
  (months/years approximate).

### 3.7 Custom converters & validation

```python
from typing import Annotated, Sequence
from cyclopts import App, Parameter, Token, validators

app = App()
UNITS = {"kb": 1024, "mb": 1024**2, "gb": 1024**3}

def bytesize(_type, tokens: Sequence[Token]) -> int:
    s = tokens[0].value.lower()
    try:
        return int(s)
    except ValueError:
        number, suffix = s[:-2], s[-2:]
        return int(number) * UNITS[suffix]

@app.command
def zero(size: Annotated[int, Parameter(converter=bytesize)], *,
         at_least: Annotated[int, Parameter(validator=validators.Number(gte=0))] = 0):
    assert size >= at_least, "size below minimum"
```

### 3.8 Convenience types

Cyclopts ships handy pre‑typed aliases (e.g. `cyclopts.types.NonNegativeInt`)
and rich validators (`validators.Path`, `validators.Number`).

______________________________________________________________________

## 4) User classes: dataclasses, Pydantic, attrs, TypedDict

Cyclopts can bind nested structures directly. Dot‑notation exposes fields,
supports **flattening** namespaces with `Parameter(name="*")`, and can **hide
keys** with `Parameter(accepts_keys=False)`.

```python
from dataclasses import dataclass
from typing import Annotated, Literal
from cyclopts import App, Parameter

app = App()

@dataclass
class User:
    name: str
    age: int
    region: Literal["us", "ca"] = "us"

@app.default
def show(user: Annotated[User, Parameter(name="*")]):
    print(user)
```

Docstrings on classes and fields feed the help page. For Pydantic models,
Cyclopts defers to Pydantic for coercion.

______________________________________________________________________

## 5) Groups & validators (across parameters)

Use `Group` to structure help and enforce cross‑field constraints (e.g.
mutually exclusive flags) without littering command bodies.

```python
from typing import Annotated
from cyclopts import App, Group, Parameter, validators

app = App()
vehicle = Group("Vehicle (choose one)",
                validator=validators.LimitedChoice(),
                default_parameter=Parameter(negative=""))

@app.command
def create(*,
           car: Annotated[bool, Parameter(group=vehicle)] = False,
           truck: Annotated[bool, Parameter(group=vehicle)] = False):
    ...
```

Set `Group.sort_key` or use `Group.create_ordered()` to control panel ordering
in help. Assign `help_formatter` per group when a bespoke layout is required.

______________________________________________________________________

## 6) Help: docstrings in, beautiful help out

- Cyclopts parses **reStructuredText** by default; toggle via `App.help_format`
  ("plaintext", "markdown", or "rst").
- Customize presentation with `App(help_formatter=...)` or per‑group
  `help_formatter`. Built‑ins: `DefaultFormatter` (rich panels) and
  `PlainFormatter` (ASCII‑only / screen‑reader‑friendly).
- Short description comes from the first docstring line; parameter docs pull
  from a standard *Parameters* section.

______________________________________________________________________

## 7) Shell completion (bash, zsh, fish)

For packaged apps:

```python
from cyclopts import App
app = App(name="myapp")
app.register_install_completion_command()  # adds --install-completion
if __name__ == "__main__":
    app()
```

```bash
myapp --install-completion         # one‑time install for current shell
```

During development for scripts not on `$PATH`, use the **wrapper**:

```bash
cyclopts run app.py --help
cyclopts run app.py:app sub --flag
```

Scripts can also be generated or installed programmatically with
`App.generate_completion()` / `App.install_completion()`.

______________________________________________________________________

## 8) Config: TOML, environment, and friends

Attach providers to `App.config` to inject defaults before parsing:

```python
from cyclopts import App, config

app = App(
    name="character-counter",
    config=[
        config.Toml("pyproject.toml",
                    root_keys=["tool", "character-counter"],
                    search_parents=True),
        config.Env("CHAR_COUNTER_"),
    ],
)
```

- TOML mapping uses `[tool.<app>.<command>]` sections by convention (toggle
  keys via provider args).
- Env var mapping is `PREFIX_<COMMAND>_<PARAM>`, with `-` → `_`.
- Build a **meta app** (next section) to let `--config /path/to/file` select a
  config at runtime.

______________________________________________________________________

## 9) Async commands

Annotate commands `async def ...`. Cyclopts runs them on an event loop (backend
defaults to `asyncio`). When **already inside** an async context, call
`await app.run_async([...])`.

______________________________________________________________________

## 10) Lazy loading (faster startup)

Register commands as import paths so modules load only when needed:

```python
user_app = App(name="user")
user_app.command("myapp.commands.users:create")
user_app.command("myapp.commands.users:delete")
app.command(user_app)
```

This defers imports until help generation or invocation.

______________________________________________________________________

## 11) Calling apps, exits, and return values

```python
app = App(result_action="return_value")   # don’t sys.exit; return value instead
@app.command
def add(a: int, b: int) -> int: return a + b
rv = app(["add", "2", "3"])           # 5
```

When `result_action` is not set, Cyclopts mirrors installed entry‑point
behaviour: printing non‑integers and exiting with an appropriate code. Control
error policy with:

- `exit_on_error` (default True) → call `sys.exit(1)` on Cyclopts errors.
- `print_error` (default True) → show rich, user‑friendly errors.
- `help_on_error` (default False) → print help before the error.
- `verbose` (default False) → include developer‑oriented detail.

______________________________________________________________________

## 12) Meta apps (session wrappers)

A meta app wraps the main application to parse **session parameters** and then
forward remaining tokens. Common uses: selecting config files, authenticating
once, injecting a shared client object, or adding global tracing.

```python
from typing import Annotated
from cyclopts import App, Parameter

app = App()

@app.command
def whoami(user: str):
    print(user)

@app.meta.default
def launcher(*tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
            user: str):
    # do auth / logging / inject defaults here
    app(tokens)  # forward to the real app

if __name__ == "__main__":
    app.meta()
```

Add `@app.meta.command` when a command should bypass the wrapper.

______________________________________________________________________

## 13) Help customization in practice

Use the rich defaults for most apps, then opt‑in to plain text for TTYs that
dislike ANSI or for accessibility. Per‑group `help_formatter` is ideal for
emphasising “Required” vs “Optional” panels.

______________________________________________________________________

## 14) Testing patterns

- Set `result_action="return_value"` in a test‑only `App` to avoid `sys.exit`.
- Disable exit/printing temporarily:
  `app(tokens, exit_on_error=False, print_error=False)` and assert on raised
  exceptions.
- Capture stdout/stderr as normal; Cyclopts uses separate consoles for output
  and errors.

Example:

```python
from cyclopts import App

app = App(result_action="return_value")

@app.command
def add(a: int, b: int) -> int:
    return a + b

def test_add():
    assert app(["add", "2", "3"]) == 5
```

______________________________________________________________________

## 15) Packaging notes (brief)

- Put `app()` under `if __name__ == "__main__":` for `python -m pkg` launches.
- Provide a console entry point in the packaging config so `myapp` lands on
  PATH.
- Use `App.version` or expose `__version__`/package metadata for `--version`.

______________________________________________________________________

## 16) A worked mini‑CLI

```python
# imgtool/__main__.py
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from cyclopts import App, Group, Parameter, validators, config

app = App(
    name="imgtool",
    help="Manipulate images",
    default_parameter=Parameter(negative="--no-"),
    config=[config.Toml("pyproject.toml", root_keys=["tool", "imgtool"])],
)

paths = Group.create_ordered("Paths")
opts  = Group.create_ordered("Options")

@dataclass
class Resize:
    width: int
    height: int

@Parameter(name="*")
@dataclass
class Global:
    verbose: bool = False
    profile: Literal["debug", "release"] = "debug"

@app.command(group="Transforms")
def resize(src: Annotated[Path, Parameter(group=paths, validator=validators.Path(exists=True))],
           dst: Annotated[Path, Parameter(group=paths)],
           *,
           size: Annotated[Resize, Parameter(name="size")],
           global_: Annotated[Global, Parameter(name="*")]):
    """Resize an image.

    Parameters
    ----------
    src: Path
        Input image path.
    dst: Path
        Output image path.
    size.width: int
        Target width.
    size.height: int
        Target height.
    global_.verbose: bool
        Verbose logging.
    """
    if global_.verbose:
        print(f"Resizing {src} -> {dst} to {size.width}×{size.height} [{global_.profile}]")
    # process...

if __name__ == "__main__":
    app()
```

CLI:

```bash
imgtool resize input.png out.png --size.width 640 --size.height 480 --verbose
# or, with flattening and config:
imgtool resize input.png out.png --width 640 --height 480
```

______________________________________________________________________

## 17) Patterns & recommendations

- **Prefer docstrings** over inline help strings; keep function signatures
  clean.
- **Flatten config** with a `@Parameter(name="*")` dataclass and pass it as a
  keyword‑only parameter to many commands.
- **Group parameters** to highlight required vs optional and to enable
  cross‑field validators.
- **Use Literals** for user‑facing choices; fall back to Enums only when a
  numeric value is meaningful to the program.
- **Adopt lazy loading** for large apps or when help generation/import cost is
  noticeable.
- **Install completion** in development via `cyclopts run ...` and in
  production via `--install-completion`.
- **In tests**, set `result_action="return_value"` and assert directly on
  results.

______________________________________________________________________

## 18) Gotchas

- **Mixing positional and keyword** follows normal Python rules; once a later
  param is supplied by keyword, earlier ones cannot be passed positionally.
- **Lists**: positional lists stop at an option unless
  `allow_leading_hyphen=True`; with keywords, tokens must complete an element
  or the parser raises a missing-argument error.
- **Mutable defaults**: do not default list/dict params to `[]/{}`; prefer
  `None` and handle inside the function, or rely on `--empty-<name>` for
  explicit empty lists.
- **Docstrings and renamed parameters**: document the Python variable name, not
  the CLI alias.

______________________________________________________________________

## 19) Cross‑referenced API surface (quick index)

- `App(...)`: `help`, `version`/`version_flags`, `default_parameter`,
  `group_*`, `help_formatter`, `config`, `exit_on_error`, `print_error`,
  `help_on_error`, `verbose`, `result_action`.
- `@app.command`, `@app.default`, `app.command(App(...), name="*" )`.
- `Parameter(...)`: `name`, `alias`, `name_transform`, `help`, `converter`,
  `validator`, `group`, `negative`, `count`, `allow_leading_hyphen`,
  `consume_multiple`, `accepts_keys`, `parse`.
- `Group(...)`: `validator`, `default_parameter`, `help_formatter`, `sort_key`.
- Config: `config.Toml`, `config.Env`, plus in‑memory dicts.
- Utilities: `cyclopts.run`, `cyclopts.edit` (spawn editor),
  `app.register_install_completion_command`, `app.install_completion`,
  `app.generate_completion`.

______________________________________________________________________

### Licence & acknowledgements

Cyclopts is authored by Brian Pugh. Refer to the project’s licence for details.
This guide is an auxiliary aid; consult the official docs for canonical
behaviour.
