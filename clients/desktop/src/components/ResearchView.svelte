<script lang="ts">
  import {
    Flame,
    ArrowRight,
    FileText,
    ShieldCheck,
    Square,
    Copy,
    Check,
    MessageSquareText,
    RotateCcw,
  } from '@lucide/svelte';
  import {
    findingStatus,
    researchPreview,
    rebuttalLetter,
    reviewDecisionCurrent,
    responseSignature,
  } from '../lib/research.ts';
  import type {
    ResearchSession,
    ResearchSnapshot,
    MockResearch,
    ResponseAction,
    Heat,
  } from '../lib/research.ts';
  import type { WorkspaceState } from '../lib/types.ts';
  let {
    snapshot,
    researchSnapshot,
    session,
    service,
    oncontext,
  }: {
    snapshot: WorkspaceState;
    researchSnapshot: ResearchSnapshot;
    session: ResearchSession;
    service: MockResearch;
    oncontext: () => void;
  } = $props();
  let mode = $state('roast');
  let external = $state(false);
  let selectedRound = $state('');
  let selectedFinding = $state('');
  let scenario = $state<'normal' | 'pushback' | 'mixed'>('normal');
  let copied = $state(false);
  let copyError = $state('');
  let acknowledged = $state(false);
  let lastDecisionInput = '';
  const sourceChat = $derived(snapshot.chats.find((c) => c.id === session.chatId)!);
  const project = $derived(snapshot.projects.find((p) => p.id === sourceChat.projectId));
  const authoring = $derived(session.mode === 'author');
  const plan = $derived(researchPreview(snapshot, session, researchSnapshot.totalRounds));
  const round = $derived(
    session.rounds.find((r) => r.id === selectedRound) ?? session.rounds.at(-1),
  );
  const findings = $derived(round?.reviewers.flatMap((r) => r.findings) ?? []);
  const finding = $derived(findings.find((f) => f.id === selectedFinding) ?? findings[0]);
  const ownBusy = $derived(researchSnapshot.busy?.chatId === session.chatId);
  const stale = $derived(
    round &&
      (round.revision !== session.revision ||
        round.paper !== session.paper ||
        round.context !== plan.context),
  );
  const resolved = $derived(
    round
      ? findings.filter((f) =>
          ['Rebuttal accepted', 'Addressed by revision', 'Limitation accepted'].includes(
            findingStatus(round, f, session, plan.context),
          ),
        ).length
      : 0,
  );
  const letter = $derived(rebuttalLetter(session, external ? undefined : round));
  const decisionInput = $derived(
    JSON.stringify([
      session.chatId,
      round?.id,
      session.revision,
      plan.context,
      session.paper,
      findings.map(responseSignature),
    ]),
  );
  $effect(() => {
    if (lastDecisionInput !== decisionInput) {
      lastDecisionInput = decisionInput;
      acknowledged = false;
    }
  });
  function respond(patch: { action?: ResponseAction; response?: string; evidence?: string }) {
    if (round && finding) service.respond(session.chatId, round.id, finding.id, patch);
    copied = false;
  }
  async function copyLetter() {
    try {
      await navigator.clipboard.writeText(letter);
      copied = true;
      copyError = '';
    } catch {
      copyError = 'Copy is unavailable. Select the letter text to copy it.';
    }
  }
</script>

<section class="research-view" aria-label="Research workspace">
  <div class="research-heading">
    <div>
      <span class="research-eyebrow">RESEARCH · SIMULATED</span>
      <h1>A tougher look at your work.</h1>
      <p>Separate reviewers. Specific objections. A place to make your case.</p>
    </div>
    <button class="secondary-button" onclick={oncontext}
      ><FileText size={15} />Project context</button
    >
  </div>
  <div class="research-mode-tabs" aria-label="Research options">
    <button
      class:active={mode === 'roast' && !authoring}
      aria-pressed={mode === 'roast' && !authoring}
      disabled={ownBusy}
      onclick={() => {
        mode = 'roast';
        service.configure(session.chatId, { mode: 'existing' });
      }}><Flame size={17} />Roast my paper</button
    ><button
      class:active={mode === 'rebuttal'}
      aria-pressed={mode === 'rebuttal'}
      onclick={() => (mode = 'rebuttal')}><MessageSquareText size={17} />Rebuttal</button
    >
    <button
      class:active={mode === 'roast' && authoring}
      aria-pressed={mode === 'roast' && authoring}
      disabled={ownBusy}
      onclick={() => {
        mode = 'roast';
        service.configure(
          session.chatId,
          session.mode === 'author'
            ? { mode: 'author' }
            : { mode: 'author', reviewerIds: ['auto', 'auto'] },
        );
      }}><FileText size={17} />Draft + review</button
    >
  </div>
  {#if mode === 'rebuttal' && ownBusy}<button
      class="secondary-button"
      onclick={() => service.stop()}><Square size={13} />Stop review</button
    >{/if}
  {#if mode === 'rebuttal' && round}<div class="rebuttal-source-toggle">
      <button class="text-button" onclick={() => (external = !external)}
        >{external ? 'Use roast findings' : 'Paste an external review instead'}</button
      >
    </div>{/if}
  {#if mode === 'roast'}
    <div class="roast-setup">
      <div class="paper-editor glass">
        <div class="paper-editor-heading">
          <span><FileText size={15} />Paper draft</span><span class="revision-label"
            >v{session.revision}{stale ? ' · changed since roast' : ''}</span
          >
        </div>
        <input
          class="paper-title"
          aria-label="Paper title"
          value={session.title}
          maxlength="160"
          disabled={ownBusy && (authoring || researchSnapshot.busy?.kind === 'reconcile')}
          oninput={(event) => service.update(session.chatId, 'title', event.currentTarget.value)}
        /><textarea
          aria-label="Paper draft"
          class="paper-text"
          rows="9"
          value={session.paper}
          maxlength="16000"
          readonly={authoring || (ownBusy && researchSnapshot.busy?.kind === 'reconcile')}
          oninput={(event) => service.update(session.chatId, 'paper', event.currentTarget.value)}
        ></textarea>
        {#if authoring}<label class="field-label author-question"
            >Research question<textarea
              class="field-textarea"
              aria-label="Research question"
              rows="3"
              maxlength="4000"
              disabled={ownBusy}
              value={session.question}
              oninput={(event) =>
                service.update(session.chatId, 'question', event.currentTarget.value)}
            ></textarea></label
          >
          <p class="panel-description author-question">
            Start freezes the question and sources first. The author creates a scripted v1, then
            both reviewers receive the identical frozen draft. Actual fit is rechecked after
            authoring.
          </p>{/if}
        <div class="paper-editor-footer">
          <span>Memory only · sample-paper findings</span><span
            >{session.paper.length.toLocaleString()} characters</span
          >
        </div>
      </div>
      <div class="roast-controls glass">
        <div class="heat-heading">
          <label for="roast-heat">How hot?</label><span class={`heat-badge ${session.heat}`}
            ><Flame size={14} />{session.heat === 'gentle'
              ? 'Gentle'
              : session.heat === 'spicy'
                ? 'Spicy'
                : 'Scorched'}</span
          >
        </div>
        <input
          id="roast-heat"
          aria-label="Roast heat"
          class="heat-slider"
          type="range"
          min="1"
          max="3"
          step="1"
          value={['gentle', 'spicy', 'scorched'].indexOf(session.heat) + 1}
          oninput={(event) =>
            service.update(
              session.chatId,
              'heat',
              (['gentle', 'spicy', 'scorched'] as Heat[])[Number(event.currentTarget.value) - 1],
            )}
        />
        <div class="heat-scale"><span>Constructive</span><span>Merciless</span></div>
        <p class="heat-description">
          {session.heat === 'gentle'
            ? 'Patient and constructive. Every objection still needs support.'
            : session.heat === 'spicy'
              ? 'Direct, skeptical and specific. Make every claim earn its place.'
              : 'Pull no punches on the paper. Challenge the claims, never the author.'}
        </p>
        <p class="heat-standard">Heat changes tone. Severity and evidence standards stay fixed.</p>
        {#if authoring}<label class="field-label reviewer-field"
            >Author<select
              aria-label="Research author"
              value={session.authorId}
              disabled={ownBusy}
              onchange={(event) =>
                service.configure(session.chatId, { authorId: event.currentTarget.value })}
              ><option value="auto">Auto · distinct eligible fixture</option
              >{#each snapshot.models as model}<option value={model.id}
                  >{model.name} · {model.place}</option
                >{/each}</select
            ></label
          >{/if}
        {#each [0, 1] as slot}<label class="field-label reviewer-field"
            >Reviewer {slot + 1}<select
              aria-label={`Reviewer ${slot + 1}`}
              value={session.reviewerIds[slot]}
              disabled={ownBusy}
              onchange={(event) =>
                service.reviewer(session.chatId, slot, event.currentTarget.value)}
              ><option value="auto">Auto · distinct eligible fixture</option
              >{#each snapshot.models as model}<option value={model.id}
                  >{model.name} · {model.place === 'local'
                    ? model.fit
                      ? model.state
                      : 'fit unavailable'
                    : 'Cloud'}</option
                >{/each}</select
            ></label
          >{/each}
        <label class="field-label criteria-field"
          >Review for<input
            aria-label="Review criteria"
            value={session.criteria}
            oninput={(event) =>
              service.update(session.chatId, 'criteria', event.currentTarget.value)}
            maxlength="1000"
          /></label
        >
        <div class="review-plan">
          <ShieldCheck size={15} /><span
            >{plan.roles.every((r) => r.place === 'local')
              ? 'Local-only reviewer plan'
              : 'Includes cloud reviewers'}<small
              >{authoring
                ? '1 author + 2 blind reviews + 1 optional check / reconciliation'
                : '2 blind reviews + 1 optional check / reconciliation'} · sequential</small
            ></span
          >
        </div>
        <p class="review-price">
          {plan.upper
            ? `$${plan.upper.toFixed(2)} maximum reserved · fictional`
            : 'No external inference charge in this local fixture'}
        </p>
        {#if ownBusy}<button class="secondary-button wide" onclick={() => service.stop()}
            ><Square size={13} />Stop review</button
          >{:else}<button
            class="primary-button roast-start wide"
            disabled={plan.blocked}
            title={plan.reason}
            onclick={() => {
              selectedRound = '';
              selectedFinding = '';
              service.start(session.chatId);
            }}
            ><Flame size={16} />{authoring
              ? 'Start draft + review'
              : session.rounds.length
                ? 'Start another roast'
                : 'Roast my paper'}<ArrowRight size={16} /></button
          >{/if}
        {#if plan.blocked && !ownBusy}<p class="review-block" role="status">{plan.reason}</p>{/if}
        <details class="advanced-preferences">
          <summary>Provenance and review outcomes</summary>{#if !authoring}<label
              class="field-label"
              >Known paper author<select
                aria-label="Known paper author"
                disabled={ownBusy}
                value={session.paperAuthor}
                onchange={(event) =>
                  service.configure(session.chatId, { paperAuthor: event.currentTarget.value })}
                ><option value="unknown">Unknown / external provenance</option><option value="human"
                  >Human-authored fixture</option
                >{#each snapshot.models as model}<option value={model.id}>{model.name}</option
                  >{/each}</select
              ></label
            >{/if}
          <p class="panel-description">
            Known authors and same-version aliases cannot fill an independent reviewer slot.
            Different models can still share errors.
          </p>
          <label class="field-label"
            >Demo blind-review outcome<select
              aria-label="Demo blind-review outcome"
              disabled={ownBusy}
              value={session.scenario}
              onchange={(event) =>
                service.configure(session.chatId, {
                  scenario: event.currentTarget.value as ResearchSession['scenario'],
                })}
              ><option value="normal">Normal findings</option><option value="reviewer-fails"
                >Reviewer 2 fails</option
              ><option value="missing-evidence">Missing source evidence</option><option
                value="disagreement">Different assessments</option
              ></select
            ></label
          >
        </details>
      </div>
    </div>
    <div class="research-scope">
      <span
        ><ShieldCheck size={14} />This target, instructions and {plan.sources.length} explicit source
        entries</span
      ><span>Other profiles, unselected content and Temporary source chats excluded</span>
    </div>
    <details class="research-manifest glass">
      <summary
        >Source manifest · {session.scope === 'project'
          ? 'whole project selected'
          : session.scope === 'custom'
            ? 'custom selection'
            : 'current selected context'}</summary
      >
      <div class="history-actions">
        <button
          class="secondary-button"
          disabled={ownBusy}
          onclick={() => service.scope(session.chatId, 'selected')}
          >Use current selected context</button
        ><button
          class="secondary-button"
          disabled={!project || ownBusy}
          onclick={() => service.scope(session.chatId, 'project')}
          >Select whole project explicitly</button
        >
      </div>
      <p class="panel-description">
        Whole project expands this visible manifest. Missing/unsupported entries and excessive
        context block the pass; nothing is silently omitted, crawled or uploaded.
      </p>
      {#if project}<div class="manifest-grid">
          <div>
            <h3>Project copies</h3>
            {#each project.files as file}<label class="manifest-entry"
                ><input
                  type="checkbox"
                  disabled={ownBusy}
                  checked={(session.scope === 'selected'
                    ? sourceChat.selectedFiles
                    : session.fileIds
                  ).includes(file.id)}
                  onchange={() => service.source(session.chatId, 'files', file.id)}
                /><span
                  >{file.name}<small
                    >v{file.revision} · {file.status} · {file.ownership === 'unbound-link'
                      ? 'unbound link'
                      : file.localOnly
                        ? 'Local-only copy'
                        : 'copy'}</small
                  ></span
                ></label
              >{/each}
          </div>
          <div>
            <h3>Saved chat transcripts</h3>
            {#each snapshot.chats.filter((c) => c.projectId === project.id) as chat}<label
                class="manifest-entry"
                ><input
                  type="checkbox"
                  disabled={ownBusy}
                  checked={session.chatIds.includes(chat.id)}
                  onchange={() => service.source(session.chatId, 'chats', chat.id)}
                /><span
                  >{chat.title}<small
                    >{chat.messages.length} messages{chat.archived ? ' · archived' : ''} · unsent draft
                    excluded</small
                  ></span
                ></label
              >{/each}
          </div>
        </div>{:else}<p class="panel-description">
          Temporary Review uses only its target and explicit instructions. Save to a project before
          adding named source copies/transcripts.
        </p>{/if}
      <p class="panel-description">
        ~{plan.tokens} sample input tokens / 6,144. Effective instructions:
      </p>
      <p class="manifest-instructions">{plan.instructions}</p>
      <div class="role-preview">
        {#each plan.roles as role}<span
            >{role.kind}: {role.name} · {role.place}{role.preparation
              ? ' · loads at Start'
              : ''}</span
          >{/each}
      </div>
    </details>
  {/if}
  {#if session.notice}<p class="research-notice" role="status">{session.notice}</p>{/if}
  {#if round && !(mode === 'rebuttal' && external)}
    <div class="review-results-heading">
      <div>
        <h2>{mode === 'roast' ? 'The roasts' : 'Build your rebuttal'}</h2>
        <p>Original findings stay frozen. A revised draft needs its own review.</p>
      </div>
      <select
        aria-label="Review round"
        value={round.id}
        onchange={(event) => {
          selectedRound = event.currentTarget.value;
          selectedFinding = '';
        }}
        >{#each session.rounds as item}<option value={item.id}
            >Round {item.number} · v{item.revision} · {item.heat}</option
          >{/each}</select
      >
    </div>
    <div class="review-status-row">
      <span class:complete={round.status === 'complete'} class="review-state"
        >{ownBusy
          ? snapshot.reviewActivity?.phase
          : round.status === 'incomplete'
            ? 'Review incomplete'
            : 'Blind review complete'}</span
      ><span>{resolved} / {findings.length} findings addressed for v{session.revision}</span
      >{#if stale}<span class="stale-review"
          >Roast evaluated v{round.revision}; current draft v{session.revision}</span
        >{/if}
    </div>
    {#if round.author}<p class="research-notice">
        Author: {round.author.name} · {round.author.place} · {round.author.status}. Question/source
        freeze precedes authored v{round.revision}. Reviewer packets contain no peer verdicts.
      </p>{/if}
    <div class="reviewer-receipts">
      {#each round.reviewers as reviewer}<div>
          <span class:cloud={reviewer.place === 'cloud'} class="location-dot"></span><strong
            >{reviewer.name}</strong
          ><span>{reviewer.place} · {reviewer.status}</span>
        </div>{/each}
    </div>
    {#if findings.length}
      <div class="findings-workbench glass">
        <nav class="findings-list" aria-label="Review findings">
          {#each findings as item, index}<button
              class:active={finding?.id === item.id}
              aria-pressed={finding?.id === item.id}
              onclick={() => (selectedFinding = item.id)}
              ><span class="finding-index">{String(index + 1).padStart(2, '0')}</span><span
                ><strong>{item.title}</strong><small>{item.severity} · {item.reviewer}</small><em
                  >{findingStatus(round, item, session, plan.context)}</em
                ></span
              ></button
            >{/each}
        </nav>
        {#if finding}<div class="finding-detail">
            <div class="finding-detail-heading">
              <span class="severity-badge">{finding.severity}</span><span
                >Original roast · v{round.revision} · {finding.locator}</span
              >
            </div>
            <h3>{finding.title}</h3>
            <blockquote>{finding.critique}</blockquote>
            <p class="requested-resolution">
              <strong>What would address this</strong>{finding.request}
            </p>
            <div class="response-editor">
              {#if finding.carriedFrom}<p class="panel-description">
                  Reply carried from {finding.carriedFrom} as a draft. It needs a fresh check; no prior
                  acceptance carries forward.
                </p>{/if}
              <label class="field-label"
                >Your approach<select
                  aria-label="Response approach"
                  value={finding.action}
                  onchange={(event) =>
                    respond({ action: event.currentTarget.value as ResponseAction })}
                  ><option value="rebut">Rebut / refute the criticism</option><option value="revise"
                    >Revise the paper</option
                  ><option value="limit">Accept and state a limitation</option><option
                    value="evidence">Need more evidence</option
                  ></select
                ></label
              ><label class="field-label"
                >Point-by-point response<textarea
                  aria-label="Finding response"
                  rows="4"
                  placeholder="State the objection, your answer and why it holds…"
                  value={finding.response}
                  oninput={(event) => respond({ response: event.currentTarget.value })}
                  maxlength="2500"></textarea></label
              ><label class="field-label"
                >Evidence or change locator<input
                  aria-label="Evidence or change locator"
                  placeholder="e.g. Methods §2, Table 1, source excerpt…"
                  value={finding.evidence}
                  oninput={(event) => respond({ evidence: event.currentTarget.value })}
                  maxlength="500"
                /></label
              >
            </div>
            <div class="finding-decision">
              <span>{findingStatus(round, finding, session, plan.context)}</span><button
                class="secondary-button"
                disabled={findingStatus(round, finding, session, plan.context) !==
                  'Ready for your decision'}
                onclick={() => service.closeFinding(session.chatId, round.id, finding.id)}
                ><Check size={14} />Accept this resolution</button
              >
            </div>
          </div>{/if}
      </div>
      <div class="rebuttal-check-bar">
        <div>
          <strong>Check your responses</strong>
          <p>
            One optional check by {round.reviewers[0].name}. It sees both critiques and your replies
            after blind review; it does not speak for every original reviewer.
          </p>
        </div>
        <div>
          <label class="field-label"
            >Demo check outcome<select aria-label="Demo rebuttal outcome" bind:value={scenario}
              ><option value="normal">Evidence-based fixture</option><option value="pushback"
                >Reviewer pushes back</option
              ><option value="mixed">Mixed / disagreement</option></select
            ></label
          ><button
            class="primary-button"
            disabled={Boolean(round.check || round.reconciliation || round.optionalClosed) ||
              round.status !== 'complete' ||
              Boolean(snapshot.attempt || snapshot.reviewActivity) ||
              findings.some((f) => !f.response.trim())}
            onclick={() => {
              mode = 'rebuttal';
              external = false;
              service.checkResponses(session.chatId, round.id, scenario);
            }}
            ><MessageSquareText size={16} />{round.check?.status === 'checking'
              ? 'Checking…'
              : round.check || round.reconciliation || round.optionalClosed
                ? 'Check used this round'
                : 'Check rebuttals'}</button
          >
        </div>
      </div>
      <div class="reconcile-option">
        <div>
          <strong>Or reconcile the reviewed draft</strong>
          <p>
            Use the same optional call to ask {round.author?.name ?? round.reviewers[0].name} to incorporate
            committed findings. The scripted v2 is unreviewed; old decisions cannot transfer.
          </p>
        </div>
        <button
          class="secondary-button"
          disabled={Boolean(
            round.check ||
            round.reconciliation ||
            round.optionalClosed ||
            snapshot.attempt ||
            snapshot.reviewActivity,
          ) ||
            round.status !== 'complete' ||
            Boolean(stale)}
          onclick={() => service.reconcile(session.chatId, round.id)}
          >Reconcile into unreviewed v2</button
        >
      </div>
      {#if round.reconciliation}<p class="response-check-receipt">
          {round.reconciliation.model} · reconciliation {round.reconciliation.status} · based on v{round
            .reconciliation.revision}. This uses the optional call instead of a response check.
        </p>{/if}
      {#if round.reconciliation?.paper}<details class="review-manifest">
          <summary>Retained reconciliation proposal · unreviewed</summary>
          <pre class="frozen-paper">{round.reconciliation.paper}</pre>
        </details>{/if}
      {#if round.check}<p class="response-check-receipt">
          {round.check.model} · {round.check.status} · checked v{round.check.revision}. Suggested
          resolutions require your decision. {round.unknownHold
            ? `$${round.unknownHold.toFixed(2)} fictional unknown cost held.`
            : ''}
        </p>{/if}
      {#if mode === 'rebuttal'}<div class="rebuttal-letter glass">
          <div class="paper-editor-heading">
            <span><FileText size={15} />Rebuttal letter · draft</span><button
              class="secondary-button"
              onclick={copyLetter}
              >{#if copied}<Check size={14} />Copied{:else}<Copy size={14} />Copy letter{/if}</button
            >
          </div>
          <pre>{letter}</pre>
          {#if copyError}<p role="status">{copyError}</p>{/if}
        </div>{/if}
    {:else}<p class="pending-findings">
        {round.status === 'incomplete'
          ? 'The review stopped before any findings. Choose eligible reviewers and start explicitly again.'
          : 'The reviewers are working separately on the frozen sample packet…'}
      </p>{/if}
    {#if !round.optionalClosed && !round.check && !round.reconciliation}<button
        class="text-button"
        disabled={ownBusy}
        onclick={() => service.finishRound(session.chatId, round.id)}
        >Finish round without the optional model call</button
      >{/if}
    <details class="review-manifest">
      <summary>Frozen scope and fictional usage</summary>
      <p>
        Origin: {round.origin}. Target: {round.title}, v{round.revision}. Sources: {round.sources.join(
          ', ',
        ) || 'no selected source entries'}. Author: {round.author?.name ??
          (round.frozen.paperAuthor === 'unknown'
            ? 'unknown provenance'
            : round.frozen.paperAuthor === 'human'
              ? 'human fixture'
              : (snapshot.models.find((m) => m.id === round.frozen.paperAuthor)?.name ??
                round.frozen.paperAuthor))}. Unselected content, other profiles and external
        retrieval are excluded.
      </p>
      <p>
        Scripted sample-paper critiques. In-session fictional actual: ${round.actual.toFixed(2)};
        pending allowance: ${round.remaining.toFixed(2)}; unknown hold: ${round.unknownHold.toFixed(
          2,
        )}. {round.author
          ? 'Author + two reviewers + one optional call, maximum four.'
          : 'Two reviewers + one optional call, maximum three.'} No automatic next round.
      </p>
    </details>
    <details class="review-manifest">
      <summary>Frozen reviewed target · v{round.revision}</summary>
      <pre class="frozen-paper">{round.paper}</pre>
    </details>
    <div class="human-review glass">
      <h2>
        {stale
          ? 'This version needs fresh review'
          : round.status === 'complete'
            ? 'Ready for your review'
            : 'Incomplete review'}
      </h2>
      <p>
        Human decisions apply only to this exact target and source manifest. Acceptance needs every
        finding addressed. It never approves the whole project, proves correctness or authorizes
        publication or execution.
      </p>
      {#if session.signoff}<p class="response-check-receipt">
          {reviewDecisionCurrent(snapshot, session) ? 'Current' : 'Stale'} human decision: {session
            .signoff.decision === 'accept'
            ? 'version accepted'
            : 'needs changes'} · v{session.signoff.revision}.
        </p>{/if}<label class="manifest-entry"
        ><input
          type="checkbox"
          aria-label="I reviewed this exact draft and source manifest"
          bind:checked={acknowledged}
          disabled={Boolean(stale) || round.status !== 'complete' || ownBusy}
        /><span>I reviewed this exact draft and source manifest.</span></label
      >
      <div class="history-actions">
        <button
          class="secondary-button"
          disabled={!acknowledged || Boolean(stale) || round.status !== 'complete' || ownBusy}
          onclick={() => service.humanDecision(session.chatId, round.id, 'changes')}
          >Record needs changes</button
        ><button
          class="primary-button"
          disabled={!acknowledged ||
            Boolean(stale) ||
            round.status !== 'complete' ||
            ownBusy ||
            !findings.length ||
            resolved !== findings.length}
          onclick={() => service.humanDecision(session.chatId, round.id, 'accept')}
          >Accept this reviewed version</button
        >
      </div>
    </div>
  {:else if mode === 'rebuttal'}
    <div class="external-rebuttal glass">
      <h2>Have reviewer comments already?</h2>
      <p>
        Paste the comments and write your response. Original reviewer identity and acceptance remain
        unknown.
      </p>
      <label class="field-label"
        >Reviewer comments<textarea
          aria-label="External reviewer comments"
          rows="5"
          value={session.importedComments}
          oninput={(event) =>
            service.update(session.chatId, 'importedComments', event.currentTarget.value)}
          maxlength="4000"
          placeholder="Paste a review or list the objections…"></textarea></label
      ><label class="field-label"
        >Your response<textarea
          aria-label="External review response"
          rows="5"
          value={session.importedResponse}
          oninput={(event) =>
            service.update(session.chatId, 'importedResponse', event.currentTarget.value)}
          maxlength="4000"
          placeholder="Reply with evidence, changes or a reasoned disagreement…"></textarea></label
      >{#if letter}<div class="rebuttal-letter">
          <div class="paper-editor-heading">
            <strong>Rebuttal letter · draft</strong><button
              class="secondary-button"
              onclick={copyLetter}><Copy size={14} />{copied ? 'Copied' : 'Copy letter'}</button
            >
          </div>
          <pre>{letter}</pre>
          {#if copyError}<p role="status">{copyError}</p>{/if}
        </div>{/if}<button class="text-button" onclick={() => (mode = 'roast')}
        ><RotateCcw size={13} />Get a fresh roast of the sample paper</button
      >
    </div>
  {/if}
  <p class="research-disclosure">
    Reviews and checks are scripted sample outcomes, even for edited or pasted text. No models run
    or sources are verified. Closing findings records your decision about a version; it never
    approves the whole project or proves the paper correct.
  </p>
</section>
