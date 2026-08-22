"""Which of the CLI's commands does no test in this suite so much as type?

ADR-054 is the reason this exists. Ten orphaned lines sat inside
`avs bench-criterion`, below the `typer.Exit` that fires when the kill
criterion fires, so the command raised `NameError` on every run where the
project was HEALTHY and worked only when it was not. It shipped that way
across eleven recorded bench runs, and that record's own explanation is one
sentence:

    Nothing caught it because no test invoked the command. `evaluate()` has
    coverage; the CLI path around it had none.

That is a claim about a population of 78 commands, established from a sample
of one. This asks it of all of them, and it is a helper, like
`write_without_reader` — `test_a_command_no_test_types.py` is what runs it,
and the allowlist there is where the judgment lives.

The bar is deliberately the weakest one available: a command counts as
covered if its name appears inside ANY string literal in ANY test file.
Not invoked, not asserted on — *typed*. A weak bar makes the negative
unarguable, and the negative is the whole product: a command no test file
even mentions cannot be under test by any mechanism, including the ones this
scan is too crude to see (`invoke(app, [name, *args])` with `name` from a
parametrisation, which a stricter scan would miss and report as a defect).

Two things this refuses to do, both of them lessons paid for already:

  * Report from no measurement. If `cli.py` is not where it is expected, or
    parses to zero commands, or there are no test files to scan, this raises.
    ADR-067's finding is that an empty measurement reads exactly like a
    passing one, and "0 commands are unnamed" is what both look like.

  * Read its own answer. The caller passes `exclude`, and the allowlist file
    MUST exclude itself. ADR-060 hit this precise trap from the other side:
    a bare string literal counted as a read, so a test's own allowlist was
    supplying every reader it then asserted existed. An allowlist of thirty
    command names, in a file this scan reads, names all thirty of them.
"""

from __future__ import annotations

import ast
import pathlib

CLI_REL = "src/ai_venture_studio/cli.py"


def _typed_name(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """The name a user types, or None if this function is not a command.

    Not the function name: `@app.command("bench-criterion")` over
    `def bench_criterion`. Keying the report on the function name would
    silently mis-key it against the thing people actually run.

    `@app.callback()` is excluded on purpose. The root callback is not a
    command anyone types and it has no name of its own to be named by.
    """
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        if not (isinstance(func, ast.Attribute) and func.attr == "command"):
            continue
        for arg in dec.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                return arg.value
        for kw in dec.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
        # Typer's default: underscores in the function name become dashes.
        return node.name.replace("_", "-")
    return None


def declared_commands(repo: pathlib.Path, cli_rel: str = CLI_REL) -> dict[str, int]:
    """Every `@app.command()` in cli.py, as typed name -> line number."""
    cli = repo / cli_rel
    if not cli.exists():
        raise FileNotFoundError(
            f"{cli_rel} is not where this audit expects it. Refusing to "
            "report 0 unnamed commands from a file that is not there."
        )
    tree = ast.parse(cli.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = _typed_name(node)
            if name:
                out[name] = node.lineno
    if not out:
        raise RuntimeError(
            f"{cli_rel} parsed to zero commands. Either the decorator moved "
            "or this scan no longer understands it; both make every command "
            "look covered, which is the failure mode this audit is for."
        )
    return out


def commands_named_in_tests(
    repo: pathlib.Path,
    names: set[str],
    *,
    exclude: set[pathlib.Path] = frozenset(),
    tests_rel: str = "tests",
) -> dict[str, set[str]]:
    """command -> the test files whose source contains it inside a string.

    Over-collects on purpose. This detector's job is to argue AGAINST a
    command being untested, so every ambiguity resolves in the direction of
    "someone typed it".
    """
    tests_dir = repo / tests_rel
    excluded = {p.resolve() for p in exclude}
    found: dict[str, set[str]] = {}
    scanned = 0
    for path in sorted(tests_dir.rglob("*.py")):
        if path.resolve() in excluded:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8")
        rel = path.relative_to(repo).as_posix()
        for name in names:
            if f'"{name}"' in text or f"'{name}'" in text:
                found.setdefault(name, set()).add(rel)
    if not scanned:
        raise RuntimeError(
            f"no test files under {tests_rel}/ after exclusions — every "
            "command would read as unnamed, which is a measurement of "
            "nothing rather than a finding about everything."
        )
    return found


def unnamed_commands(
    repo: pathlib.Path,
    *,
    exclude: set[pathlib.Path] = frozenset(),
    cli_rel: str = CLI_REL,
    tests_rel: str = "tests",
) -> dict[str, int]:
    """Declared commands that no scanned test file so much as types."""
    declared = declared_commands(repo, cli_rel)
    named = commands_named_in_tests(
        repo, set(declared), exclude=exclude, tests_rel=tests_rel
    )
    return {n: line for n, line in sorted(declared.items()) if n not in named}
