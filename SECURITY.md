# Security Policy

## Supported versions

`thingskit` doesn't yet have a stabilized version cycle (`0.x`): a single
line is maintained, the one on the `master` branch. There is no earlier
version supported in parallel.

| Version | Supported |
| ------- | --------- |
| `master` (latest) | ✅ |
| any earlier version | ❌ |

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.** A public
issue exposes the problem before a fix exists.

Instead, email **donaldo@sowell.app** with:

- a description of the problem and its impact;
- steps to reproduce it;
- the version (commit) affected.

You'll receive an acknowledgment within a few days. Once available, the
fix is published in a new release, and the vulnerability is mentioned in
the announcement once the fix has shipped — not before.

## Scope relevant to this project

`thingskit` reads and writes to the local Things 3 database and relies on
macOS TCC (Transparency, Consent and Control) consent and an Apple code
signing chain to work reliably (see `constitution.md`, in French, §
Sensitive zones). The most useful reports concern:

- any bypass of the launcher's code-identity check (`bin/thingskit`, the
  guard described in § Sensitive zones #3);
- any direct write path into the Things SQLite database (the project never
  writes raw SQL — such a path would be a design flaw, not a feature);
- any leak or privilege escalation tied to the signed bundle or the build
  script (`build/bundle.py`).

## Out of scope

Bypassing the launcher's code-identity check by an **attacker who already
controls the user account** is an accepted residual, not a vulnerability:
`constitution.md` (in French) § Sensitive zones documents that the shim doesn't verify
itself (ADR-002 § Decision 5bis), and that nothing prevents someone who
already controls the account from replacing it with an ad hoc, validly
signed binary that ignores the seal. The core guard (hardened runtime,
CDHash) protects against **modifying** a signed binary, not against
**replacing** it — that's not the same property, and the latter assumes an
attacker already established on the machine. A report describing only
this scenario, without prior compromise of the account, doesn't provide
new information.

In scope: any way to achieve this effect **without** already controlling
the user account (privilege escalation, remote bypass, etc.).

## Additional channel

GitHub also offers
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
(the repository's *Security* tab) as an alternative to the email above.
