# Project Conventions

Code style and structure rules for this project. The SDD spec methodology lives in
`specs/README.md`; this file is the *code* conventions that specs' Interface sections
and `/spec-impl` must follow.

## Directory Layout

    src/
      <package>/          # main package
        __init__.py
        <module>.py
      tests/
        __init__.py
        test_<module>.py
    specs/                # SDD specs (see specs/README.md)
    CONVENTIONS.md        # this file

Use the `src/` layout: the package lives under `src/`, tests under `src/tests/`.

## Modules & Packages

- One primary class or function per module; name the module after its primary content.
- Every package has an `__init__.py`.
- Prefer small, single-responsibility modules over large catch-all files.

## Naming

- Modules: `snake_case`
- Classes: `PascalCase`
- Functions / variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private (module-internal): single leading underscore, `_name`

## Type Hints & Docstrings

- Public functions and classes require full type hints (parameters and return).
- Public functions and classes require a docstring (Google style: summary line, then
  `Args:`, `Returns:`, `Raises:` as applicable).
- Docstrings carry `# Spec:` references (format in `specs/README.md`).

## Imports

- Absolute imports from the package root.
- No circular imports; if a cycle appears, refactor rather than work around it.
- Group order, blank line between groups: stdlib, third-party, local.

## Error Handling

- Raise specific exceptions; never a bare `except:`.
- Public functions document their `Raises:` in the docstring.
- No silent swallowing of exceptions.

## Testing

- Framework: `pytest`.
- Test file `src/tests/test_<module>.py` mirrors `src/<package>/<module>.py`.
- One test function per behavior; name `test_<unit>_<scenario>`.
- Test functions carry `# TestSpec:` references (format in `specs/README.md`).
- Shared fixtures live in `src/tests/conftest.py`, not in test modules.

## Formatting

- 4-space indent; max line length 100.
- Trailing newline at end of file.
