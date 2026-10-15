# Plugin capability workflow qualification — 2026-10-15

## Source and scope

Implementation branch: `mehrdad/plugin-capabilities-20261015`.
Runtime implementation reviewed and tested at `7e05486ca8892bded89d0107a0cf8637a0304d3c`,
against baseline `bf0e6c8`. Later tracker/report-only commits do not alter runtime behavior.
The plugin bundle is version 0.2.0; the development router package remains
2.0.0a1, so callers must check command availability rather than version alone.

This is synthetic workflow qualification on macOS arm64 with Python 3.12.12,
setuptools 83.0.0, build 1.3.0 and cryptography 50.0.2. It does not establish
other Python/OS compatibility, live model quality, marketplace publication,
provider billing coverage or CI results.

## Verification

- Scope/import/manifest/link verifier: passed.
- Python syntax compilation: passed. The repository defines no standalone typechecking gate.
- Source workflow suite: 144 tests passed in 208.629 seconds.
- Thirteen new test methods cover plugin capability workflows, including four
  independent qualification-failure variants, hardware/expiry boundaries,
  legacy-history reads, policy denial before egress, consent, public receipt
  fields and conditional restoration.
- Source distribution and wheel: built with the repository-pinned backend.
- Installed-wheel workflow: 144 tests passed in 204.942 seconds outside the source checkout.
- Standards review: actionable finding fixed and reviewed again; no remaining defects reported.
- Spec review: independent negative cases and hardware participation added;
  no remaining defects reported after follow-up.

## Delivered boundaries

The onboarding skill and seven focused skills use the same CLI and connection
owner. Diagnosis binds the selected configuration to the owned generation.
Request explanations use retained opt-in metadata and retain explicit unknowns.
New history entry v2 stores versioned policy/routing evidence; v1 remains readable.

Qualification runs a fixed synthetic box-count envelope over Responses, including
structured output and tool-result replay. It uses local-only request admission,
at most four serial requests, an absolute probe deadline, bounded output and
owned transport cleanup. It neither enrolls routes nor attests which runtime
binary serves an endpoint. Supplied assets, configuration and client executable
are fingerprinted; expired or changed evidence requires requalification.

Recommendations check actor/model eligibility and a physical-memory lower bound.
They do not verify general inference memory fit, start downloads or authorize a
route. Missing hardware evidence stays unknown, including a timed-out OS probe.

Privacy and monthly configured-cost budgets are staged in a new private
configuration beside the original, then activated through the existing
preview/disconnect/connect/status workflow. Staging alone is inactive. History
consent is separate and is not transferred. Existing atomic reservations enforce
configured estimates; unknown infrastructure costs and provider invoices remain
outside those claims.

Usage insights use a single retained-history snapshot and expose no retained
text. Maintenance reuses compatibility checks and conditional restoration;
it does not install updates. The coding client retains its agent loop.

## Planning record

The approved implementation tickets are tracked separately from the original
Wayfinder decision map. Conservative implementation choices and limits are
recorded here and in the capability contract. The historical decision tickets
were not silently closed as human resolutions, and no live activation, remote
publication or merge was performed.
