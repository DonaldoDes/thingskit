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

## What the CLI does not isolate — named residuals

These are **known and accepted**, written down so that they stay findable.
A risk left unsaid is a risk that comes back as a surprise.

- **The Things URL-scheme authentication token travels in an `argv`.**
  `move-task --to-heading` reads the token from the Things database
  (`TMSettings.uriSchemeAuthenticationToken`, read-only) and hands it to
  `/usr/bin/open` as part of the URL. While that child process lives, the
  full command line — token included — is readable through the process
  table (`ps`) by **any process running as the same user**. This follows
  from how `open` is invoked; the exposure window has **not** been
  measured, and no claim is made about how long it lasts. It is not
  mitigated by the output capture added on 2026-08-27: capture bounds what
  comes *back* on our file descriptors, not what the kernel exposes about a
  running process. Closing it would require a different surface than the
  URL scheme — none exists today for assigning a heading to an existing
  to-do (measured, see `constitution.md` § Sensitive zones).
- **A heading title transits through the system pasteboard.** Since
  2026-08-27 `create-heading` no longer types the title character by
  character — `keystroke` silently mangled every composed character
  (measured: `Éprouvé — formalités é à ù ç` landed in the database as
  `aprouva — formalitas a a a ç`). It now copies the title to the pasteboard
  and pastes it with Cmd-V. While that paste is in flight, the title is
  readable by **any process that can read the pasteboard**, and pasteboard
  managers will record it in their history.

  The user's own pasteboard is **taken before the menu click and written only
  after it**, so nothing is exposed while the menu is enumerated and clicked,
  nor on the `NO_LABEL` and `LOST_FOCUS` failure paths where no paste happens.
  Three states are distinguished, and *unknown is never treated as empty*: a
  `clipboard info` that errors is a **refusal**, so is a content that cannot be
  saved, and both refusals land **before anything is overwritten and before
  anything is clicked**. The restore runs on both the success and the failure
  path, and it is **guarded**: its own failure can neither replace the error it
  accompanies (which would swallow the marker warning that a title-less heading
  may have been created) nor turn a successful write into a failure (which
  would make the caller retry, duplicating in the database). A failed restore
  is reported as a **warning**, never silently.

  Three residuals are named rather than left unsaid. **The window is not
  merely unmeasured, it is unbounded**: the System Events block around the
  paste carries no `with timeout`, and `osa()` passes no `timeout=`, so a
  System Events that stalls extends the exposure to the default AppleEvent
  timeout — two minutes. Bounding it would kill the script between the paste
  and the commit, leaving a partial heading; same trade-off as
  `_frontmost_check`, decided the same way. **If another process writes to the
  pasteboard between the take and the restore**, the restore overwrites content
  it never saved — a race that cannot be closed without tracking
  `changeCount`. And **the restore does not reproduce the flavour set
  exactly**: measured on an RTF pasteboard, `clipboard info` reports the same
  class and size before and after, but `pbpaste` returns empty before and the
  content after. The content is not lost; a plain-text pasteboard does come
  back byte for byte. "All flavours are preserved" was written on the strength
  of a text round-trip and is **refuted**.

  Closing the transit itself would require a surface that assigns a heading to
  an existing project without driving the interface. None exists — re-measured
  on 2026-08-27 with the URL-scheme token in hand: a `project` / `update`
  carrying `items` returns 0 from `open` and creates nothing.
- **Child-process error text is dropped.** Since 2026-08-27 every child is
  spawned with its output captured, and a failure is reported by **return
  code only**, never by quoting the argv. `open` distinguishes its causes
  in that text (`kLSApplicationNotFoundErr`, `-10814`) while returning `1`
  for all of them, so that distinction is lost. This is an accepted
  trade-off: the post-action verification decides the effect in every case
  and depends on no diagnostic from the launcher. Extracting the error
  class from the captured text without quoting the argv is a possible
  refinement, not a fix for a defect.

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
