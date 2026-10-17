# Versioning

The Python runtime and skills plugin have independent release versions. Bump the
affected artifact when preparing its release, choosing the largest applicable
compatibility change since its previous release. CI and test-only changes do not
automatically require a release bump, and merging a PR does not publish a release.

## Sources and mirrors

| Artifact | Canonical source | Required mirrors |
| --- | --- | --- |
| Python runtime | `project.version` in [pyproject.toml](../pyproject.toml) | `__version__` in [hormuz/__init__.py](../hormuz/__init__.py) |
| Skills plugin | `version` in [plugin.json](../plugin.json) | [.claude-plugin/plugin.json](../.claude-plugin/plugin.json), [gemini-extension.json](../gemini-extension.json) |

Use the version tool to update a release stream's source and mirrors together.
`check` validates both streams' formats and agreement. Every bump checks both
streams before writing; fix inconsistent metadata before requesting a bump.

## Choosing a number

Runtime major/minor/patch meaning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html): major for incompatible
public behavior, minor for compatible additions or deprecations, and patch for
compatible fixes. The public surface includes documented CLI commands,
configuration, routing behavior and exported Python interfaces. A prerelease
does not establish production readiness or model/client qualification.

Runtime spelling uses the
[Python packaging version scheme](https://packaging.python.org/en/latest/specifications/version-specifiers/).
This repository accepts a narrow canonical subset: `MAJOR.MINOR.PATCH`, optionally
followed by `aN`, `bN` or `rcN`. Numbers use ASCII digits without leading zeroes
except zero itself. Alpha, beta and release-candidate stages advance in that
order, followed by the final release. Epochs, development/post releases, local
labels and other normalized spellings are outside this repository's format.

The plugin uses numeric `MAJOR.MINOR.PATCH` only, matching its existing host
manifest contract. During `0.x` development, use minor for breaking changes or
added capabilities, and patch for compatible fixes or instruction wording.
Declare a stable plugin contract before choosing `1.0.0`; subsequent releases
use the same major/minor/patch compatibility meanings as the runtime.

## Commands

Run from the repository root:

```sh
python tools/version.py check
python tools/version.py bump runtime prerelease --dry-run
python tools/version.py bump runtime prerelease --pre beta --dry-run
python tools/version.py bump runtime minor --pre alpha --dry-run
python tools/version.py bump plugin patch --dry-run
```

Remove `--dry-run` to apply a reviewed change. The command updates only that
artifact's source and mirrors. Review the diff, run `check`, then follow
[CONTRIBUTING.md](../CONTRIBUTING.md) for validation.

| Operation | Result |
| --- | --- |
| `major`, `minor`, `patch` | Increment that base component, resetting lower components. The result is final unless `--pre alpha`, `--pre beta` or `--pre rc` starts the selected stage at 1. |
| Runtime `prerelease` | Keep the base version and increment its current stage number. `--pre` may select the same stage or promote to a later stage at 1; earlier stages are rejected. A final version is rejected. |
| Runtime `final` | Remove the prerelease suffix, keeping the base version. A final version or any `--pre` option is rejected. |
| Plugin bump | Accept only `major`, `minor` or `patch`, without `--pre`. |

For example, runtime `prerelease` advances `2.0.0a2` to `2.0.0a3`; promotion with
`--pre beta` produces `2.0.0b1`. `final` produces `2.0.0`. A numeric patch bump
instead produces `2.0.1`, or `2.0.1a1` with `--pre alpha`. These examples describe
transitions; the canonical files determine the current versions.

`--dry-run` makes no changes. For a disposable checkout, the optional global
argument selects its metadata root: `python tools/version.py --root PATH check`.
The tool never commits, tags or publishes. Existing release/tag protections and
release review still apply; this tool does not establish that a number is
available on an external registry.

## Other versioned records

Schema and durable-record integer versions describe individual compatibility
contracts. They advance only when their own contract changes, with the required
fixture, migration and rollback documentation. A runtime or plugin bump does not
change them. Supported external client pins describe independently qualified
client versions and require their own qualification. Preserve historical release
numbers, fixtures and qualification evidence instead of replacing them during a
release bump.
