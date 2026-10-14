# Research workflows through skills

Decision: 2026-10-14. The owner approved the glossy project workspace and Auto bar,
then requested that research and other specialised workflows live in skills rather
than dedicated UI modes. The Research screen, bespoke review state machine,
rebuttal editor and scripted multi-reviewer engine are retired from the current UI.
The [earlier exploration](https://github.com/Xpounder-com/hormuz/blob/1eb59d65d92164533af9eb18b511c30193eedfd0/docs/RESEARCH_REVIEW_MODE.md)
remains historical evidence, not the active desktop scope.

## First skill boundary

The core UI owns projects, chats, selected context, setup, preferences and the
minimal model/Auto bar. Context → Skills enables a bundled instruction pack for a
project or Temporary chat. A leading command explicitly activates it in the normal
prompt. Typing only previews the effective instructions; it makes no model,
classifier, tool, file, download or network request. Changing destination uses that
destination's skill availability. Imported projects require explicit reselection.

| Pack | Command | Purpose |
| --- | --- | --- |
| [Paper roast](../clients/desktop/skills/paper-roast/SKILL.md) | `/roast` | Grounded objections; gentle/spicy/scorched tone supplied in the prompt. Heat never changes severity or evidence requirements. |
| [Paper rebuttal](../clients/desktop/skills/paper-rebuttal/SKILL.md) | `/rebuttal` | Point-by-point responses, revisions, limitations and missing evidence using supplied reviews. |
| [Evidence review](../clients/desktop/skills/evidence-review/SKILL.md) | `/review` | Distinguish evidence, inference, contradictions and gaps in supplied work. |

These are instruction-only `SKILL.md` packs with a bounded checked-in compiled
catalog. They are visible in effective context and frozen with submitted
instructions. They grant no file/workspace access, tools, execution authority,
model swaps, cloud consent, spending or publication. This first slice does not
install arbitrary marketplace packages or execute repository code.

A single model using a review skill is a single-model critique. It must not invent
second reviewers, citations, experiments, checks or acceptance. Users can explicitly
choose another model in the normal picker. A changed paper is unreviewed until a
new review; a drafted reply is not a verified refutation. Final decisions are human.

## Later execution capabilities

#502 now tracks reusable instruction packs and their context/provenance tests.
#503 tracks the optional generic skill-execution boundary, including bounded
multi-model delegation where explicitly requested. Neither is a prerequisite for
the first Electron/local-chat path or a dedicated research UI.

A future multi-model skill must use the same host-owned scheduler/admission and
replaceable model adapters as ordinary chat. It needs explicit scoped sources,
finite roles/calls/allowance, known model identities, identical blind initial
packets where requested, immutable source/version provenance, cancellation,
partial/unknown accounting and visible incomplete/disagreement outcomes. It cannot
repeat until agreement, call an author its independent reviewer, restore stale
permissions or turn model/human review text into tool authority.

Agent runtime adapters remain a separate later capability in the
[Agent roadmap](AGENT_EXECUTION_ROADMAP.md). Research and coding procedures may be
skills; actual tools, sessions, custody and spending still belong to qualified
host/engine contracts. No real multi-model skill execution is claimed here.
