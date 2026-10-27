# Native Skill resource closures

`scripts/native_skill_closure.py` builds and checks an offline, source-pinned
resource bundle for one native Skill entry. It does not install a plugin,
change Agent discovery aliases, execute linked commands, access credentials,
fetch URLs, publish a release, or grant execution/write authority.

An isolated Skill subtree may reference resources outside its own directory.
Verifying every file inside that subtree establishes its integrity, but does
not establish that the entry's required resources are available.

## Ownership and caller input

This setup capability owns the generic packaging, bounded traversal, integrity
checks and discovery-path verification. It accepts a caller-supplied, reviewed
profile; it does not choose a concrete Agent's business rules or fixed release.

The target Skill owner maintains the meaning of mandatory and conditional
resources. The Agent/runtime owner maintains the exact source revision, profile,
selected task conditions, publication configuration and rollback evidence.
A profile must reflect those owners' reviewed declarations and source evidence.
Condition names are opaque selectors to this tool; selecting one does not grant
permission to execute the associated capability or perform external writes.

## Source and topology

The build reads Git objects from the profile's exact 40-character commit. Dirty
checkout files, current `main`, PATH Git replacements, Git hooks and network
lookups cannot supply resource bytes. Source symlinks and gitlinks are rejected.
Missing local objects fail closed: Git lazy-fetch is disabled and all transport
protocols are denied even if repository-local config enables a remote helper.
Resources retain canonical repository-relative paths. This illustrative layout
uses generic names, not a supplied installation profile:

```text
bundle/
  closure-manifest.json
  skills/example-domain/sample-skill/SKILL.md
  skills/example-domain/sample-skill/references/...
  skills/other-domain/shared-skill/references/shared-resource.md
```

An approved installer may later point a discovery alias at the actual
`bundle/skills/example-domain/sample-skill` directory. It must not flatten that
directory or put dependency files in an unrelated ambient ancestor. The OS
follows a directory symlink before processing subsequent `..` components;
lexical `normpath(alias / "../..")` can point somewhere different. `verify-entry`
checks literal reference traversal through the supplied alias and compares the
result with the approved physical resource target. Tests exercise real symlinks.
The actual harness must load the verified physical entry and dependency paths;
a unit test does not establish what a running model loaded.

## Explicit, bounded dependency selection

The caller's profile declares every external local Markdown resource target.
Internal entry-subtree resources are included in full; their local links and the
selected dependencies' own links are traversed recursively. An external target
not declared in the profile fails closed. File count, total bytes and graph
depth are bounded. A selected declaration does not authorize recursive scanning
or installing every Skill in a category.

The bounded scanner handles inline Markdown links and single-line reference
definitions with bare targets, including collapsed and shortcut references.
Every definition is checked even if its label is unused. Empty/multiline
definitions and unsupported local target syntax (including titles/angle brackets)
fail closed. Top-level fenced code follows the delimiter rules in
[CommonMark §4.5](https://spec.commonmark.org/0.31.2/#fenced-code-blocks): an
opening run has at least three identical backticks or tildes and zero to three
leading spaces. A closing run uses the same character, is at least as long,
and has only spaces/tabs after it. A backtick info string cannot contain a
backtick; an unclosed top-level block remains literal through document end.
Recognizable blockquote/list-prefixed or further-indented fence forms fail
closed; container-block grammar is not implemented. This is not a complete
Markdown/HTML interpreter: the selected source/profile review must inventory its
resource references, and must not infer semantic closure for other markup from
a build success or a source pin alone.

`mandatory` resources must be reachable and present. Named conditions require
explicit `--enable` selection; every target declared for a selected condition
must also be reachable and included. An existing but unreachable selected target
fails before a bundle is created. A deferred target is still checked to be a
real, regular file at the pinned source revision, but its bytes are not bundled.
The receipt lists all missing conditions. An installer/admission preflight must
require the conditions actually needed by the task; it cannot interpret a
successful core check as support for every conditional capability. If an enabled
condition leads to an undeclared, absent or out-of-bounds transitive dependency,
the entire build fails. Extending that closure requires a source-reviewed
profile update, not an ambient filesystem fallback or a modified old release.

A profile may classify an exact source-file/link pair as
`external-project-evidence`, with source evidence and rationale, when the link
belongs to the consuming project's context rather than the Skill bundle. The
manifest and receipt record that declaration without supplying the target. It
does not prove the consuming project has the document or permit skipping that
project's evidence check. Any undeclared missing link remains an error.

## Build and independently verify

Run from the canonical source repository or a clean owning worktree. Supply the
absolute path to the caller's reviewed profile and a new physical output path:

```bash
python3 skills/agent-harness/buzz-agent-setup/scripts/native_skill_closure.py build \
  --repo . \
  --profile /absolute/path/to/reviewed-profile.json \
  --output /absolute/physical/path/new-skill-bundle
```

`--output` must not exist. The builder exclusively creates a new directory,
writes read-only resources and writes the manifest last. A failed build removes
only that newly created output. This is not atomic production publication:
consumers must not activate an output until the build and independent verification
have completed. Keep existing immutable releases and aliases untouched.

The deterministic manifest binds resource source commit, profile digest,
builder-content digest, categorized entry path, conditions, exact file bytes,
Git modes and reference edges. Its digest must be frozen by the normal source
and build review. Verification requires that external trusted digest; reading
the candidate's own digest and passing it back is not independent approval.

For valid CLI syntax, `build`, `verify` and `verify-entry` emit one JSON receipt
to stdout and exit zero on success. A resource/profile validation failure,
including malformed identity, condition or resource-record types, emits
`status: blocked` with a reason and exits one. Argument-usage errors retain
argparse's separate stderr/exit-two behavior. Unexpected programming errors
are not converted by a broad `TypeError` handler.

```bash
python3 skills/agent-harness/buzz-agent-setup/scripts/native_skill_closure.py verify \
  --bundle /absolute/physical/path/new-skill-bundle \
  --expected-manifest-sha256 REVIEWED_MANIFEST_SHA256

python3 skills/agent-harness/buzz-agent-setup/scripts/native_skill_closure.py verify-entry \
  --bundle /absolute/physical/path/new-skill-bundle \
  --entry /absolute/path/to/discovery/sample-skill \
  --expected-manifest-sha256 REVIEWED_MANIFEST_SHA256
```

Add `--require-condition optional-mode`, for example, when that caller-declared
condition is actually required; a core-only bundle fails that preflight. Building
an expanded bundle uses the corresponding `--enable optional-mode` and must
close every selected resource's mandatory links. The condition name in this
example is illustrative and must be replaced by a reviewed profile declaration.

Verification rejects extra/missing files, symlink files/directories, content or
mode drift, a wrong discovery target, missing included reference targets and an
unmet required condition. These integrity checks are not isolation against a
malicious same-UID owner changing files concurrently. Production publication
still needs reviewed immutable ownership, the existing runtime/identity gates,
scoped quiescence/CAS/rollback, alias and fresh-loading readback, then the owning
workflow's genuine business-task acceptance. This packaging tool does not alter
workflow execution state or waive recovery constraints.

## Tests and acceptance boundary

```bash
TMPDIR=/private/tmp python3 -m unittest discover \
  -s skills/agent-harness/buzz-agent-setup/tests \
  -p 'test_native_skill_closure.py'
```

Tests use real temporary Git objects and directory symlinks, not mocked resource
contents. They cover fixed-source reads despite a dirty checkout; mandatory
recursive links; deferred and required conditions; path/command rejection;
missing, undeclared or symlink source targets; budgets; trusted-digest checks;
extra/mutated/symlink bundle files; discovery binding; and the intact-but-incomplete
old flat layout. A real filtered partial clone with a promisor pack and missing
blobs verifies that no configured marker-only transport helper runs; local
reference-style definitions exercise the same declaration/recursive gates.
The suite also covers shorter/different inner fence delimiters, closing-tail
rules, unsupported container fences, actual subprocess stdout/exit contracts for
all three CLI operations, and malformed profile/manifest types. On macOS, the
physical temporary-directory spelling avoids unrelated `/var` → `/private/var`
ancestor differences in legacy resolver tests.

An offline bundle/link test proves resource construction and lookup only.
Deployment, actual model loading and business-task results require separate
runtime and workflow evidence from their respective owners.
