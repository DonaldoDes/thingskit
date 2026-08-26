# thingskit

A CLI for [Things 3](https://culturedcode.com/things/) (Cultured Code) on
macOS. A single script (`bin/thingskit`) that reads the Things database
directly in SQLite and writes through the least invasive application
surface available (URL scheme, targeted AppleScript, or, as a last resort,
UI automation).

## Why this project exists

`thingskit` was built for a specific use case: giving a software agent (an
agentic harness) access to Things that is **versioned, readable, and
tested** — as opposed to a script regenerated on the fly on every call,
which is never twice the same and therefore never reliable. It remains
usable from the command line by a human, but its design (fast SQLite reads,
contractual exit codes, systematic optional JSON output) carries the marks
of that original use case.

## ⚠️ Two things to know before you clone

### The interface is in French

**The tool itself speaks French, end to end.** Every message
(`existe déjà :`, `introuvable :`, `AMBIGU :`, `tâche(s)`), every
`argparse` help string (`titre exact du projet ou de l'area`,
`échéance DURE, rare par nature`), every docstring — French throughout.
This English README exists so the project can be found by someone
searching for "Things 3 CLI" or "AppleScript automation"; it is not a
promise that the tool itself has been, or will be, translated. The design
doctrine (`constitution.md`) is French too, for the same reason: it is over
1,500 lines of dense doctrine, costly to translate and to keep in sync in
two languages. If a French-speaking interface is a blocker for you, know
that before you invest time here.

### You can't run a self-built binary without your own signing identity

**This repository cannot be run as-is after a plain clone, and a bundle
you build yourself will not work either without an Apple signing
certificate that belongs to you.**

`bin/thingskit` is never executed directly: the `PATH` entry
(`~/.local/bin/thingskit`) delegates to a signed `.app`
(`/Applications/thingskit.app`), which embeds its own Python interpreter.
This indirection exists for a precise reason: macOS grants TCC
(Transparency, Consent and Control — the "Full Disk Access" permission
needed to read the Things database) consent to a **code identity**, not to
a file path. A shebang script delegates that identity to the system's
Python interpreter, whose signature changes on every update
(`brew upgrade python`, for instance) — and consent granted yesterday
becomes invalid tomorrow, without warning. Packaging `thingskit` into a
signed bundle under a stable identity was the only way to make that
consent durable.

The launcher and the bundle therefore verify, on every startup, that the
binary being run carries `CFBundleIdentifier = app.sowell.thingskit` and
the Apple Team ID `56AP2NSB54` — the maintainer's own. **A bundle you build
yourself, signed under your own developer identity (or ad hoc), will fail
this check and the launcher will refuse to start.** This is not a bug or
an arbitrary restriction: it's the guard that protects TCC consent from
silent code substitution (full detail: `constitution.md` § Sensitive
zones, #3 — French).

**In practice**: you can read the code, run it under test (the suite
depends on no installed bundle), and contribute. You cannot, as things
stand, obtain a runnable `thingskit` outside the maintainer's own machine
without rebuilding this chain of trust with your own signing identity —
something the project does not yet generalize. This is an open, tracked
piece of work (see `BUG-010` in the project history: generalizing the
hardcoded signing identity). If this blocks you, open an issue before
investing time in a setup that cannot succeed today.

## Requirements

- macOS (the project relies on AppleScript, direct SQLite reads on Things'
  Group Container, and Apple code signing — none of which exists on any
  other platform).
- [Things 3](https://culturedcode.com/things/) installed and opened at
  least once (so its database exists).
- Python ≥ 3.11 to run the tests. The built executable embeds its own
  interpreter and needs nothing else once built.

## Installation

```bash
git clone git@github.com:DonaldoDes/thingskit.git
cd thingskit
uv sync --group dev   # or: pip install -e '.[dev]'
```

See the usability limit above before attempting to build and sign a
runnable bundle.

## Commands

Read commands (read-only SQLite, fast, do not require Things to be
running):

- `areas`, `projects`, `headings`, `tasks`, `agenda`, `uuid`, `find-task`,
  `deeplink` — each accepts `--json` for scriptable output.

Write commands (URL scheme, targeted AppleScript, or UI automation as a
last resort):

- `create-area`, `create-project`, `create-heading`, `add-task`,
  `delete-task`, `complete-task`, `cancel-task`, `reopen-task`,
  `rename-task`, `reschedule-task`, `move-task`, `move-project`,
  `set-notes`, `append-notes`.

Every subcommand documents its exact behavior (and the observed Things
behavior it was measured against) in the docstring at the top of
`bin/thingskit`. `thingskit <subcommand> --help` gives the detailed usage
— in French, per the note above.

## Tests

```bash
uv run pytest -q
# or, if the environment is already activated:
python3 -m pytest -q
```

The suite depends on neither Things 3 nor an installed bundle: each test
builds its own disposable SQLite database and simulates AppleScript calls.
A subset of tests (signing, packaging, code-identity guard) is
automatically skipped when the installed bundle or the required macOS
tools (`codesign`, `security`, `openssl`) are absent — see
`tests/conftest.py`.

## Contributing

See `CONTRIBUTING.md` for the contribution flow (fork + pull request),
commit conventions, and the TDD requirement. `constitution.md` (in
French) documents the project's complete technical doctrine — it's the
reference to read before any substantial change, especially around
sensitive zones (Things database access, code identity, AppleScript
writes).

## License

MIT — see [`LICENSE`](LICENSE).
