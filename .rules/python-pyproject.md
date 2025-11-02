# 1. Overview of `uv` and `pyproject.toml`

Astral's `uv` project manager (short for "universal virtualenv") is a
Rust-based project and package manager that uses Tom's Obvious Minimal Language
configuration stored in `pyproject.toml` as its central configuration. Running
commands like `uv init`, `uv sync` or `uv run` will prompt `uv` to:

1. Look for a `pyproject.toml` in the project root and keep a lockfile
   (`uv.lock`) in sync with it.
2. Create a virtual environment (`.venv`) if one does not already exist.
3. Read dependency specifications (and any build-system directives) to install
   or update packages accordingly. (Astral Docs[^1], RidgeRun.ai[^2])

In other words, the project's `pyproject.toml` drives everything—from metadata
to dependencies to build instructions—without needing `requirements.txt` or a
separate `setup.py` file. (Level Up Coding[^3], Python Packaging[^4])

______________________________________________________________________

## 2. The `[project]` Table (Python Enhancement Proposal 621)

The `[project]` table is defined by Python Enhancement Proposal 621 and is now
the canonical place to declare metadata (name, version, and authors), and
runtime dependencies. At minimum, Python Enhancement Proposal 621 requires:

- `name`
- `version`

However, most projects benefit from including the following additional fields
for clarity and compatibility:

```toml
[project]
name = "my_project"            # Project name (Python Enhancement Proposal 621 requirement)
version = "0.1.0"              # Initial semantic version
description = "A brief overview"       # Short summary
readme = "README.md"           # Path to the project readme file (automatically included)
requires-python = ">=3.10"     # Restrict Python versions, if needed
license = { text = "MIT" }     # Software Package Data Exchange compatible license expression or file
authors = [
  { name = "Alice Example", email = "alice@example.org" }
]
keywords = ["uv", "astral", "example"]   # (Optional) for metadata registries
classifiers = [
  "Programming Language :: Python :: 3",
  "License :: OSI Approved :: MIT License",
  "Operating System :: OS Independent"
]
dependencies = [
  "requests>=2.25",            # Runtime dependency
  "numpy>=1.23"
]
```

- **`name` and `version`:** Mandatory per Python Enhancement Proposal 621.
  (Python Packaging[^4], Reddit[^5]) Reddit[^5])
- **`description` and `readme`:** Although not mandatory, they help with
  indexing and packaging tools; `readme = "README.md"` tells `uv` (and PyPI) to
  include the readme document as the long description. (Astral Docs[^1], Python
  Packaging[^4])
- **`requires-python`:** Constrains which Python interpreters the package
  supports (e.g. `>=3.10`). (Python Packaging[^4], Reddit[^5])
- **`license`:** Specify a licence as a Software Package Data Exchange
  identifier (via `license = { text = "ISC" }`) or point to a file (e.g.
  `license = { file = "LICENSE" }`). (Python Packaging[^4], Reddit[^5])
- **`authors`:** A list of tables with `name` and `email`. Many registries
  (e.g., PyPI) pull this for display. (Python Packaging[^4], Reddit[^5])
- **`keywords` and `classifiers`:** These help search engines and package
  indexes. Classifiers must follow the exact trove list defined by PyPA.
  (Python Packaging[^4], Reddit[^5])
- **`dependencies`:** A list of Python Enhancement Proposal 508-style
  `"requests>=2.25"`). `uv sync` will install exactly those versions, updating
  the lockfile as needed. (Astral Docs[^1], RidgeRun.ai[^2])

______________________________________________________________________

## 3. Optional and Development Dependencies

Modern projects typically distinguish between "production" dependencies (those
needed at runtime) and "development" dependencies (linters, test frameworks,
etc.). Python Enhancement Proposal 621 introduces
`[project.optional-dependencies]` for this purpose:

```toml
[project.optional-dependencies]
dev = [
  "pytest>=7.0",        # Testing framework
  "black",              # Code formatter
  "flake8>=4.0"         # Linter
]
docs = [
  "sphinx>=5.0",        # Documentation builder
  "sphinx-rtd-theme"
]
```

- **`[project.optional-dependencies]`:** Each table key (e.g. `dev`, `docs`)
  defines a "dependency group." Install a group via `uv add --group dev` or
  `uv sync --include dev`. (Python Packaging[^4], DevsJC[^6])
- **Why use groups?** The lockfile stays deterministic (via `uv.lock`) while
  still separating concerns (test-only versus production). (Medium[^7],
  DevsJC[^6])

______________________________________________________________________

## 4. Entry Points and Scripts

Projects that expose command-line interfaces or graphical user interfaces can
use the `[project.scripts]` and `[project.gui-scripts]` tables defined in
Python Enhancement Proposal 621:

```toml
[project.scripts]
mycli = "my_project.cli:main"    

[project.gui-scripts]
mygui = "my_project.gui:start"
```

- **`[project.scripts]`:** Defines console scripts. Running `uv run mycli`
  invokes the `main` function in `my_project/cli.py`. (Astral Docs[^8])
- **`[project.gui-scripts]`:** On Windows, `uv` will wrap these in a graphical
  user interface executable; on Unix-like systems, they behave like normal
  console scripts. (Astral Docs[^8])
- **Plugin Entry Points:** When the project supports plugins, use
  `[project.entry-points.'group.name']` to register them. (Astral Docs[^8])

______________________________________________________________________

## 5. Declaring a Build System

Python Enhancement Proposals 517 and 518 require a `[build-system]` table to
describe how the project should be built and installed. A "modern" convention
is to specify `setuptools>=61.0` (for editable installs without `setup.py`) or
a lighter alternative like `flit_core`. Below is the typical setup using
setuptools:

```toml
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"
```

- **`requires`:** A list of packages needed at build time. Editable installs in
  `uv` rely on at least `setuptools>=61.0` and `wheel`. (Python Packaging[^4],
  Astral Docs[^8])
- **`build-backend`:** The entry point for the build backend.
  `setuptools.build_meta` provides a Python Enhancement Proposal 517 compliant
  backend for setuptools. (Python Packaging[^4], Astral Docs[^8])
- **Note:** Omitting `[build-system]` makes `uv` assume
  `setuptools.build_meta:__legacy__` and still install dependencies, but the
  tool will not editably install the project unless `tool.uv.package = true`
  (see next section). (Astral Docs[^8])

______________________________________________________________________

## 6. `uv`-Specific Configuration (`[tool.uv]`)

Astral `uv` exposes additional settings through `[tool.uv]`. The most common
option is:

```toml
[tool.uv]
package = true
```

- **`tool.uv.package = true`:** Forces `uv` to build and install the project
  into its virtual environment every time `uv sync` or `uv run` executes.
  Without this, `uv` only installs dependencies (not the project itself) if
  `[build-system]` is missing. (Astral Docs[^8])
- Additional `uv`-specific keys (e.g., custom indexes, resolver policies) can
  live under `[tool.uv]`, but `package` is the most common. (Python
  Packaging[^4], Astral Docs[^8])

______________________________________________________________________

## 7. Putting It All Together: Example `pyproject.toml`

Below is a complete example that demonstrates all sections. Adjust values as
needed for an individual project.

```toml
[project]
name = "my_project"
version = "0.1.0"
description = "An illustrative example for Astral uv"
readme = "README.md"
requires-python = ">=3.10"
license = { text = "MIT" }
authors = [
  { name = "Alice Example", email = "alice@example.org" }
]
keywords = ["astral", "uv", "pyproject", "example"]
classifiers = [
  "Programming Language :: Python :: 3",
  "License :: OSI Approved :: MIT License",
  "Operating System :: OS Independent"
]
dependencies = [
  "requests>=2.25",
  "numpy>=1.23"
]

[project.optional-dependencies]
dev = [
  "pytest>=7.0",
  "black",
  "flake8>=4.0"
]
docs = [
  "sphinx>=5.0",
  "sphinx-rtd-theme"
]

[project.scripts]
mycli = "my_project.cli:main"

[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.uv]
package = true
```

**Explanation of key points:**

1. **Metadata under `[project]`:**

   - `name`, `version` (mandatory per Python Enhancement Proposal 621)
     (Python Packaging[^4], Reddit[^5])
   - `description`, `readme`, `requires-python`: provide clarity about the
     project and help tools like PyPI. (Python Packaging[^4], Reddit[^5])
   - `license`, `authors`, `keywords`, `classifiers`: standardized metadata,
     which improves discoverability. (Python Packaging[^4], Reddit[^5])
   - `dependencies`: runtime requirements, expressed in Python Enhancement
     Proposal 508 syntax. (Astral Docs[^1], RidgeRun.ai[^2])

2. **Optional Dependencies (`[project.optional-dependencies]`):**

   - Grouped as `dev` (for testing + linting) and `docs` (for documentation).
     Installing them is as simple as `uv add --group dev` or
     `uv sync --include dev`. (Python Packaging[^4], DevsJC[^6])

3. **Entry Points (`[project.scripts]`):**

   - Defines a console command `mycli` that maps to `my_project/cli.py:main`.
     Invoking `uv run mycli` runs the `main()` function. (Astral Docs[^8])

4. **Build System:**

   - `setuptools>=61.0` plus `wheel` ensures both legacy and editable installs
     work. ✱ Newer versions of setuptools support Python Enhancement Proposal
     660 editable installs without a `setup.py` stub. (Python Packaging[^4],
     Astral Docs[^8])
   - `build-backend = "setuptools.build_meta"` tells `uv` how to compile the
     package. (Python Packaging[^4], Astral Docs[^8])

5. **`[tool.uv]`:**

   - `package = true` ensures that `uv sync` will build and install the project
     (in editable mode) every time dependencies change. Otherwise, `uv` treats
     the repository as a collection of scripts only (no package). (Astral
     Docs[^8])

______________________________________________________________________

## 8. Additional Tips & Best Practices

1. **Keep `pyproject.toml` Human-Readable:** Edit it by hand when possible.
   Modern editors (Visual Studio Code, PyCharm) offer Tom's Obvious Minimal
   Language syntax highlighting and Python Enhancement Proposal 621
   autocompletion. (Python Packaging[^4])

2. **Lockfile Discipline:** After modifying `dependencies` or any `[project]`
   fields, always run `uv sync` (or `uv lock`) to update `uv.lock`. This
   guarantees reproducible environments. (Astral Docs[^1])

3. **Semantic Versioning:** Follow [semver](https://semver.org/) for `version`
   values (e.g., `1.2.3`). Bump patch versions for bug fixes, minor for
   backward-compatible changes, and major for breaking changes. (Python
   Packaging[^4])

4. **Keep Build Constraints Minimal:** When editable installs are unnecessary,
   omit `[build-system]` (but note that `uv` will then skip building the
   package and only install dependencies). To override, set
   `tool.uv.package = true`. (Astral Docs[^8])

5. **Use Exact or Bounded Ranges for Dependencies:** Rather than `requests`, use
   `requests>=2.25, <3.0` to avoid unexpected major bumps. (DevsJC[^6])

6. **Consider Dynamic Fields Sparingly:** Declare fields like
   `dynamic = ["version"]` only when the version is computed at build time
   (e.g. via `setuptools_scm`). Ensure the build backend supports dynamic
   metadata. (Python Packaging[^4])

______________________________________________________________________

## 9. Summary

A "modern" `pyproject.toml` for an Astral `uv` project should:

- Use the Python Enhancement Proposal 621 `[project]` table for metadata
  and `dependencies`.
- Distinguish optional dependencies under `[project.optional-dependencies]`.
- Define any command-line interface or graphical user interface entry points
  under `[project.scripts]` or `[project.gui-scripts]`.
- Declare a Python Enhancement Proposal 517 `[build-system]`
  (e.g. `setuptools>=61.0`, `wheel`, `setuptools.build_meta`) to support
  editable installs, or omit it and rely on `tool.uv.package = true`.
- Include a `[tool.uv]` section, with at least `package = true`, to direct `uv`
  to build and install the project package.

Following these conventions ensures that the project is fully Python
Enhancement Proposal compliant, easy to maintain, and integrates seamlessly
with Astral `uv`.

[^1]: [Working on projects | uv - Astral Docs](https://docs.astral.sh/uv/guides/projects/)
[^2]: [uv tutorial: a fast Python package and project manager](https://www.ridgerun.ai/post/uv-tutorial-a-fast-python-package-and-project-manager)
[^3]: [Modern Python development with pyproject.toml and uv](https://levelup.gitconnected.com/modern-python-development-with-pyproject-toml-and-uv-405dfb8b6ec8)
[^4]: [Python Packaging User Guide: writing pyproject.toml](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/)
[^5]: [Anyone used the uv package manager in production? (Reddit)](https://www.reddit.com/r/Python/comments/1ixryec/anyone_used_uv_package_manager_in_production/)
[^6]: [The Complete Guide to pyproject.toml – devsjc blogs](https://devsjc.github.io/blog/20240627-the-complete-guide-to-pyproject-toml/)
[^7]: [Start using the uv Python package manager for better dependency management](https://medium.com/%40gnetkov/start-using-uv-python-package-manager-for-better-dependency-management-183e7e428760)
[^8]: [Configuring projects | uv - Astral Docs](https://docs.astral.sh/uv/concepts/projects/config/)
