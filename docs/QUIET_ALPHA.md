# Quiet-alpha verification

Hormuz uses a small, maintainer-invited quiet alpha before broad public
promotion. The gate asks whether independent developers, security reviewers,
platform engineers, and engineering administrators can install the public
checkout and finish the provider-free demonstration using only public
repository material.

This is a release-verification exercise, not employee monitoring, a usability
study containing recordings, or a request for company data. Participation
must use synthetic data. The aggregate evidence contains no names, GitHub
handles, email addresses, comments, prompts, responses, credentials, customer
facts, local paths, hostnames, or screenshots.

## What counts as independent

An independent session uses either no help or only material already available
in the public repository, including public issues. Maintainer or private help
is allowed when someone is stuck, but that session does not count toward the
five independent completions. Record the failed session before requesting
help; do not hide or relabel it.

The first alpha image supports only Linux `amd64`. The source release gate
tests Linux with CPython 3.11 through 3.14. macOS, Windows, WSL, and source
`arm64` observations are useful compatibility reports, but they do not expand
the supported platform contract in [SUPPORT.md](../SUPPORT.md).

## Privacy and consent boundary

The invitation assigns or asks you to generate a random participant ID. It is
not an account and Hormuz never receives it during normal gateway use. Keep it
so a later session can be recognized as a return without recording your
identity in the repository:

```bash
python3 -c 'import uuid; print("qa:" + str(uuid.uuid4()))'
```

Generate a new session ID for each attempt:

```bash
python3 -c 'import uuid; print("qas:" + str(uuid.uuid4()))'
```

Return the allowlisted session block through the same private invitation
channel. Do not put the participant ID in a public issue because a GitHub
account could then become a durable identity mapping. The maintainer may
verify privately that participant IDs belong to distinct people, but must not
commit or publish that mapping.

Setting `consent_content_free_recording` to `true` means only that you consent
to the fixed metadata fields in this document entering the aggregate. It is
not consent to retain terminal output, correspondence, debugging content, or
identity. If you do not consent, do not send a session block; you may still
use and report problems with Hormuz normally.

## Independent provider-free run

Start with a clean checkout. Do not configure an OpenAI or Anthropic key for
this required path.

```bash
git clone https://github.com/Xpounder-com/hormuz.git
cd hormuz
git rev-parse HEAD
python3 --version
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --editable .
hormuz demo
```

Record whole-second installation and demonstration times with a local
stopwatch. The successful demonstration prints six fixed `PASS` lines and
reports zero external provider calls. Do not send the command output; record
only `passed`, `failed`, or `not_attempted` and one fixed failure code.

If a command fails, stop the required run at that point. Use the
[installation report](https://github.com/Xpounder-com/hormuz/issues/new?template=installation.yml)
for a sanitized public installation problem, the
[documentation report](https://github.com/Xpounder-com/hormuz/issues/new?template=documentation.yml)
for confusing instructions, or [SECURITY.md](../SECURITY.md) for sensitive
security behavior. Never include a participant ID in the report. The
maintainer records only the resulting issue or private-advisory reference in
the aggregate, with no mapping back to a participant.

## Optional real-client path

The five independent completions require no provider account. If you already
have authorized test credentials and independently choose to exercise Codex
or Claude Code, follow [CLIENTS.md](CLIENTS.md) using synthetic prompts and a
non-production account. Record only one enum:

- `openai_codex_succeeded`
- `anthropic_claude_succeeded`
- `both_succeeded`
- `failed`
- `not_attempted`

Do not send a model ID, prompt, response, token count, request ID, provider
key, Hormuz identity token, log, or screenshot. This optional tester
attestation does not replace the dedicated two-provider release evidence in
issue #115.

## Session block

Return exactly these fields through the invitation channel. Values in angle
brackets are replaced with a value from the stated fixed vocabulary; do not
add notes or extra fields.

```json
{
  "session_id": "qas:<random UUID>",
  "participant_id": "qa:<persistent random UUID>",
  "session_date": "YYYY-MM-DD",
  "persona": "developer | security | platform | engineering_admin",
  "source_commit": "<40 lowercase hexadecimal characters>",
  "package_version": "0.1.1",
  "installation_method": "source_checkout | signed_oci_digest",
  "environment": {
    "os_family": "linux | macos | windows | wsl",
    "os_major_version": "<digits only>",
    "architecture": "x86_64 | arm64",
    "python_minor": "3.11 | 3.12 | 3.13 | 3.14",
    "docker_used": false
  },
  "consent_content_free_recording": true,
  "installation_status": "passed | failed | not_attempted",
  "demo_status": "passed | failed | not_attempted",
  "assistance": "none | public_repository_material_only | maintainer_or_private_help",
  "time_to_install_seconds": 180,
  "time_to_demo_seconds": 45,
  "failure_code": "none | install_dependency | unsupported_platform | command_not_found | demo_policy | demo_network_boundary | demo_evidence | documentation | provider_path | security | other",
  "optional_provider_path": "not_attempted | openai_codex_succeeded | anthropic_claude_succeeded | both_succeeded | failed",
  "returning_session": false,
  "content_free_attestations": {
    "prompt_or_response_absent": true,
    "credential_or_token_absent": true,
    "customer_or_company_data_absent": true,
    "person_identity_absent": true,
    "local_path_absent": true,
    "free_text_absent": true
  }
}
```

Use JSON `null` for a time only when its corresponding step is
`not_attempted`. A failed step still records elapsed seconds and a failure code
other than `none`. An initial session must attempt installation. A returning
session reuses the participant ID, gets a new session ID, and occurs on a
later date; reinstalling from a clean checkout is preferred.

## Maintainer aggregation and gate

The maintainer converts consented blocks into one strict
`hormuz.quiet-alpha-evidence` v1 aggregate. Useful findings are represented
only by an opaque finding ID, category, blocker flag, public issue or private
advisory reference, status, resolution commit, and successful retest session.
Raw intake and participant-to-person mappings must stay out of Git, Actions,
release artifacts, and the aggregate.

Validate an aggregate with:

```bash
python tools/verify_quiet_alpha_evidence.py /private/path/quiet-alpha-evidence.json
```

The command exits successfully for release evidence only when all of these
conditions hold:

1. five to ten distinct people are attested privately;
2. all four personas are represented;
3. at least five distinct participants independently install and complete the
   demonstration;
4. at least one successful participant returns on a later date and completes
   another independent session;
5. every security or installation blocker has a resolution commit and a
   successful independent retest; and
6. broad promotion has not started.

The repository's fixture is deliberately synthetic:

```bash
python tools/verify_quiet_alpha_evidence.py \
  tests/fixtures/quiet_alpha/complete-synthetic-v1.json \
  --allow-synthetic-fixture
```

It can prove that the contract and validator execute. It always reports
`"ready_for_broad_promotion": false` and can never close the quiet-alpha gate.
The aggregate also cannot cryptographically prove distinct humanity, live
provider behavior, security, productivity, production readiness, or traffic
that bypassed Hormuz.
