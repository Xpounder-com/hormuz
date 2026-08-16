# Content-free DLP detector evaluation

Hormuz can measure one configured organization DLP rule against a labeled local corpus without calling OpenAI, Anthropic, the gateway, or any usage/context store. The corpus remains content-bearing. The result is a content-free aggregate artifact suitable for review and release evidence.

This command implements the evaluation mechanism required by [accepted ADR 0004](decisions/0004-structured-dlp-and-approval-boundary.md). It does not prove that a corpus is organization-representative, choose an acceptable error threshold, or promote a rule beyond detect-only. Those remain explicit security-owner decisions.

## Corpus contract

The input is UTF-8 JSONL. Blank lines are ignored. Every nonblank line is one case with exactly these members:

```json
{"payload":{"input":"synthetic.user@example.com"},"expected_match":true}
{"payload":{"input":"ordinary synthetic text"},"expected_match":false}
```

- `payload` is the complete provider-shaped JSON object to inspect. It may contain OpenAI or Anthropic messages, tool results, inline text documents, encoded text, or other JSON fields used by the selected rule.
- `expected_match` is the human-reviewed ground truth for whether the selected rule should match at least once in that payload.
- Duplicate JSON members, unknown case members, non-standard JSON constants, invalid UTF-8, non-object payloads, and non-boolean labels fail closed.
- A corpus is capped at 25 MiB and 10,000 cases. The ordinary detector nesting and encoded-payload limits still apply.
- Case identifiers are intentionally unsupported because the output does not retain per-case outcomes.

Treat this file as sensitive source material. Keep it in the organization's controlled boundary, exclude it from source control unless the examples are provably synthetic, and apply the organization's ordinary retention and access controls.

## Run an evaluation

The rule must already be enabled in the organization-level `egress_controls.dlp` configuration. `--protocol` and `--model` select an exact configured upstream route and detector scope to test; evaluation fails if either the route or rule does not apply there.

The repository includes `examples/dlp-evaluation-email.jsonl` as a deliberately synthetic format and installation smoke fixture. Do not treat its results as organization evidence.

```bash
hormuz --config /etc/hormuz/hormuz.json dlp evaluate \
  --rule-id email_address \
  --corpus-id security-email-2026-08-v1 \
  --protocol openai \
  --model gpt-5.5 \
  --input /secure/evaluations/email-labeled.jsonl \
  --output /secure/evidence/email-evaluation.json
```

`--corpus-id` is an administrator-controlled version label made only from letters, digits, `.`, `_`, `:`, `/`, and `-`. It is not computed from corpus content and is not a cryptographic attestation of the evaluated bytes. Record corpus custody and review separately if that assurance is required.

The output file is created with mode `0600` and is not overwritten unless `--force` is supplied. `--output -` writes the same JSON to stdout, so operators must ensure their shell history, terminal capture, and pipeline destination are appropriate even though the report is content-free.

## What the report contains

Schema `hormuz.dlp-evaluation.v1` includes only:

- Hormuz, Python, and deterministic-detector versions;
- DLP policy version and safe rule metadata;
- exact provider protocol and routed model under evaluation;
- caller-supplied corpus version, positive/negative case totals, and total finding count;
- true positives, true negatives, false positives, and false negatives;
- precision, recall, specificity, false-positive rate, false-negative rate, and accuracy; and
- explicit privacy and manual-promotion declarations.

A metric is `null` when its denominator is zero. The report never contains payloads, matched values, filenames, case identifiers, samples, dictionary values or environment-variable names, prompts, responses, or an unkeyed corpus hash. Validation and detector errors report only a line/case number and a bounded reason, not content.

## Interpretation and promotion boundary

The evaluator temporarily applies the chosen configured detector in `detect` mode. That measures match behavior without redacting, denying, creating approvals, forwarding provider traffic, or writing ordinary evidence. It does not test a team/person action overlay; evaluate the organization-owned detector at each applicable provider/model scope, then review stricter overlay behavior with the existing policy and gateway tests.

Before changing a lower-confidence rule from detect-only, security owners should freeze and review a corpus representative of the organization's actual languages, code, logs, tool payloads, and failure modes; record acceptable false-positive and false-negative thresholds; compare the aggregate output to those thresholds; and approve a separately reviewed policy change. Hormuz deliberately performs none of those judgment steps automatically.

The tool proves only how the configured deterministic detector behaved on the supplied local cases. It does not establish semantic detection, source-path classification, archive-content inspection, tenant isolation, causal productivity impact, or complete DLP coverage.
