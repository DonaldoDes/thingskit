# Contributing to thingskit

Thanks for your interest in this project. This document describes the
external contribution flow, which is distinct from the maintainer's own
workflow.

## An intentional divergence, not a contradiction

`constitution.md` (in French) § Git strategy describes the **maintainer's**
flow: local branches merged directly into `master` via `git merge --no-ff`,
with no pull request. This document describes the **external
contribution** flow, which is different — fork and pull request, as is
customary on any public repository. The two don't contradict each other:
they govern two different populations. An external contributor follows
this file, not the "Git strategy" section of the constitution.

## Contribution flow

1. **Fork** the repository and create your branch from `master`:
   `git checkout -b <type>/<short-description>` (`feat/`, `fix/`, or
   `docs/` depending on the nature of the change).
2. **Develop in TDD** (see § TDD requirement below).
3. **Commit** following the repository's commit conventions (see
   § Commit conventions).
4. **Open a pull request** against `master`, with a description that
   explains the *why* of the change, not just the *what*.
5. The pull request must pass CI (`.github/workflows/ci.yml`) and be
   reviewed before merging. Nothing lands on `master` without a positive
   review — that's the invariant that overrides everything else in this
   document.

## Commit conventions

Conventional commits, scoped to the ticket or feature identifier when
there is one:

```
fix(bug-016): fix --horizon filtering in agenda
feat(move-project): add the move-task subcommand
docs(us-010): add the open-source convention files
```

Expected types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`. A clear
message explains why the change was needed, not just what it changes
technically.

## TDD requirement

This project applies strict TDD, with no exception for external
contributions:

- Every behavior change (new subcommand, bug fix, modification of an
  existing function) is preceded by a test that fails for the right
  reason, before writing the code that makes it pass.
- No pull request is accepted with production code lacking a matching
  test, or with a test lacking an assertion.
- `constitution.md` (in French) documents the conventions actually
  observed in the code (one subcommand = one `cmd_<name>(a) -> int`
  function, pure logic kept separate from side effects, etc.) — a
  contribution that departs from them without explicit justification will
  be sent back in review.

## Running the test suite

```bash
uv sync --group dev
uv run pytest -q
```

The whole suite runs without Things 3 installed and without a built
bundle — see `README.md` § Tests. If your contribution touches an area
that requires macOS or code-signing tools (`codesign`, `security`,
`openssl`), the affected tests are skipped automatically when those are
absent; make sure they still pass on a machine that has them before
opening the pull request.

## Building the bundle locally

The tests need neither Things 3 nor a built bundle. Building one does need
an Apple signing certificate of your own, and a local configuration file
that this repository deliberately does not ship:

```ini
# build/identity.conf — local, git-ignored, no default is versioned
bundle_identifier = app.example.thingskit
team_identifier = <your-team-id>      # 10 uppercase alphanumerics (the leaf certificate's OU)
install_path = /Applications/thingskit.app
```

Then `python3 -m build.bundle` (or `python3 -m build.bundle <destination>`,
or `--config <file>` to point at another configuration).

Three things worth knowing before you spend time on it:

- **The three fields are mandatory and strictly shaped.** An absent, empty,
  duplicated, unknown or ill-formed field is a refusal that names the file
  and the field — never a silent default. The same shape rules are enforced
  a second time, at runtime, on the copy sealed inside the bundle.
- **You have to recreate this file after a `git clean`.** That is the
  accepted cost of shipping no identity in a public repository (`ADR-003`).
- **The floor of the code requirement is not configurable.** The
  configuration can only narrow what the bundle demands of itself: the
  Apple anchor and the certificate-type marker are written in code, and no
  configuration can remove them.

## Contributing in a sensitive zone

`constitution.md` (in French) § Sensitive zones lists the areas of the
code where a mistake has a disproportionate cost: writes to the Things
database (never in raw SQL, always through an application surface), the
signed bundle's code identity (TCC consent), resolving an ambiguous target
(uuid vs. title), waiting for an effect after an asynchronous write. Any
pull request touching one of these zones must:

- read the corresponding section of `constitution.md` before coding;
- include adversarial tests, not just happy-path tests (a test that proves
  the guard holds under an attempted bypass, not merely that it doesn't
  get in the way of the normal case);
- explain in the pull request description which sensitive zone is touched
  and why the change doesn't weaken it.

A pull request that touches a sensitive zone without these elements will
be sent back for completion before any substantive review.

## Reporting a security issue

Do not open a public issue for a vulnerability — see
[`SECURITY.md`](SECURITY.md).
