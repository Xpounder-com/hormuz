<script lang="ts">
  import { X, Plus, FileText, Trash2, ShieldCheck, ArrowUpRight } from '@lucide/svelte';
  import type { Chat, RoutePlan, WorkspaceState } from '../lib/types.ts';
  import type { MockWorkspace } from '../lib/workspace.ts';
  import { skills, activeSkills } from '../lib/skills.ts';
  let {
    snapshot,
    chat,
    plan,
    tab,
    service,
    ontab,
    onclose,
    onimport,
    onmodels,
    onconnections,
    onfile,
  }: {
    snapshot: WorkspaceState;
    chat: Chat;
    plan: RoutePlan;
    tab: string;
    service: MockWorkspace;
    ontab: (value: string) => void;
    onclose: () => void;
    onimport: () => void;
    onmodels: () => void;
    onconnections: () => void;
    onfile: (id: string) => void;
  } = $props();
  const project = $derived(snapshot.projects.find((p) => p.id === chat.projectId));
</script>

<aside class="context-panel" aria-label="Project context">
  <div class="context-header">
    <strong>{project ? 'Project context' : 'Temporary context'}</strong><button
      class="icon-button"
      aria-label="Close context"
      onclick={onclose}><X size={17} /></button
    >
  </div>
  <div class="context-tabs" role="tablist" aria-label="Context tabs">
    {#each ['Files', 'Instructions', 'Skills', 'Preferences'] as label}<button
        role="tab"
        id={`context-${label.toLowerCase()}`}
        aria-controls="context-tabpanel"
        tabindex={tab === label.toLowerCase() ? 0 : -1}
        aria-selected={tab === label.toLowerCase()}
        onclick={() => ontab(label.toLowerCase())}
        onkeydown={(event) => {
          if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
          event.preventDefault();
          const buttons = Array.from(
            event.currentTarget.parentElement!.querySelectorAll<HTMLButtonElement>('button'),
          );
          const index = buttons.indexOf(event.currentTarget);
          const next =
            buttons[
              (index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length
            ];
          ontab(next.textContent!.toLowerCase());
          next.focus();
        }}>{label}</button
      >{/each}
  </div>
  <div
    class="context-content"
    role="tabpanel"
    id="context-tabpanel"
    aria-labelledby={`context-${tab}`}
  >
    {#if tab === 'files'}
      <div class="panel-section-title">
        <h2>Included in this chat</h2>
        <span>{chat.selectedFiles.length}</span>
      </div>
      <p class="panel-description">
        Files belong to the project. Only selected copies enter a request.
      </p>
      {#if project}
        <div class="file-list">
          {#each project.files as file}
            <div class="file-row">
              <label
                ><input
                  type="checkbox"
                  checked={chat.selectedFiles.includes(file.id)}
                  disabled={(file.status !== 'available' || file.ownership === 'unbound-link') &&
                    !chat.selectedFiles.includes(file.id)}
                  onchange={() => service.toggleFile(file.id)}
                /><FileText size={17} /><span
                  ><strong title={file.name}>{file.name}</strong><small
                    >{file.status === 'available'
                      ? `v${file.revision} · ${file.localOnly ? 'Local-only' : 'Text copy'}`
                      : file.status}</small
                  ></span
                ></label
              ><button
                class="icon-button"
                aria-label={`Preview ${file.name}`}
                title="Preview, replace or remove project entry"
                onclick={() => onfile(file.id)}><ArrowUpRight size={13} /></button
              >
            </div>
          {/each}
        </div>
        <button class="secondary-button wide" onclick={onimport}
          ><Plus size={15} />Add sample file</button
        >
      {:else}<p class="empty-panel">Save to a project to add sample files.</p>{/if}
      <div class="context-summary">
        <ShieldCheck size={18} />
        <div>
          <strong>{plan.cloudAllowed ? 'Cloud-allowed context' : 'Local-only context'}</strong>
          <p>
            {plan.context.some((f) => f.localOnly)
              ? 'A selected file restricts this packet to local models.'
              : 'Privacy applies to the full selected packet.'}
          </p>
        </div>
      </div>
      <div class="packet-meta">
        <span>History</span><strong>Latest {plan.historyCount} complete exchanges</strong><span
          >Input size</span
        ><strong>~{plan.tokens.toLocaleString()} / 6,144 tokens · sample</strong><span
          >Excluded</span
        ><strong>Other chats & unselected files</strong>
      </div>
    {:else if tab === 'instructions'}
      {#if project}<label class="field-label"
          >Project instructions<textarea
            class="field-textarea"
            aria-label="Project instructions"
            value={project.instructions}
            oninput={(event) => service.setProjectInstructions(event.currentTarget.value)}
            rows="7"
            maxlength="8000"></textarea></label
        >{/if}
      <label class="field-label"
        >Chat instructions <small>Added to project instructions</small><textarea
          class="field-textarea"
          aria-label="Chat instructions"
          value={chat.instructions}
          oninput={(event) => service.setChatInstructions(event.currentTarget.value)}
          placeholder="Add guidance for this chat…"
          rows="4"
          maxlength="8000"></textarea></label
      >
      {#if chat.instructions}<button
          class="text-button"
          onclick={() => service.setChatInstructions('')}>Reset chat instructions</button
        >{/if}
      <details class="effective-instructions">
        <summary>Effective instructions</summary>
        <p>{plan.instructions}</p>
      </details>
      <p class="panel-description">
        Edits apply to the next request. Submitted instructions stay frozen.
      </p>
    {:else if tab === 'skills'}
      <h2>Skills, in your chat.</h2>
      <p class="panel-description">
        Make an instruction pack available here, then use its command with your work in the normal
        prompt.
      </p>
      {#each skills as skill}<label class="toggle-field">
          <span
            ><strong>{skill.name}</strong><small>{skill.command} · v{skill.version}</small><small
              >{skill.description}</small
            ></span
          >
          <input
            type="checkbox"
            aria-label={`${skill.name} ${skill.command}`}
            checked={(project?.skillIds ?? chat.skillIds ?? []).includes(skill.id)}
            onchange={() => service.toggleSkill(skill.id)}
          />
        </label>{/each}
      <p class="panel-description">
        <strong>Active for the next request</strong><br />
        {activeSkills(project?.skillIds ?? chat.skillIds, chat.draft)
          .map((s) => s.name)
          .join(', ') || 'No skill active in the current prompt.'}
      </p>
      <p class="panel-description">
        Skills add visible instructions to the selected model. They grant no tools, file access or
        spending, and do not run other reviewers. Imported projects require explicit skill
        selection.
      </p>
    {:else}
      <div class="panel-section-title">
        <h2>{project ? 'Project defaults' : 'Temporary session'}</h2>
      </div>
      {#if project}<label class="field-label"
          >Project model default<select
            aria-label="Project model default"
            value={project.modelIntent ?? 'auto'}
            onchange={(event) => service.setProjectIntent(event.currentTarget.value)}
            ><option value="auto">Auto</option>{#if project.modelIntent === 'unbound'}<option
                value="unbound">Unbound imported intent</option
              >{/if}{#each snapshot.models as model}<option value={model.id}>{model.name}</option
              >{/each}</select
          ></label
        ><label class="toggle-field"
          ><span
            ><strong>Enforce Local-only for this project</strong><small
              >Chat and request choices cannot loosen this restriction.</small
            ></span
          ><input
            type="checkbox"
            role="switch"
            checked={!!project.localOnly}
            onchange={(event) => service.setProjectRestriction(event.currentTarget.checked)}
          /></label
        >{/if}
      <label class="toggle-field"
        ><span
          ><strong
            >{project
              ? 'Allow cloud for this project'
              : 'Allow cloud for this Temporary chat'}</strong
          ><small>Selected prompts, files and instructions may go to a paid model.</small></span
        ><input
          type="checkbox"
          role="switch"
          checked={project?.cloudAllowed ?? chat.cloudAllowed}
          onchange={(event) => service.setCloudAllowed(event.currentTarget.checked)}
        /></label
      >
      <p class="panel-description">
        {project
          ? 'Explicit permission is separate from connecting a provider.'
          : 'Permission ends on discard. Saving requires the destination project’s permission.'} All actions
        here are simulated.
      </p>
      <label class="field-label"
        >{project ? 'Project objective' : 'App objective'}{#if project}<select
            aria-label="Project objective"
            value={project.objective}
            onchange={(event) =>
              service.setProjectObjective(
                event.currentTarget.value as 'balanced' | 'quality' | 'speed',
              )}
            ><option value="balanced">Balanced · local preference</option><option value="quality"
              >Quality</option
            ><option value="speed">Speed</option></select
          >{:else}<span class="field-readout">{snapshot.appObjective}</span>{/if}</label
      >
      <label class="field-label"
        >Chat objective<select
          aria-label="Chat objective"
          value={chat.objective}
          onchange={(event) =>
            service.setChatObjective(
              event.currentTarget.value as 'inherit' | 'balanced' | 'quality' | 'speed',
            )}
          ><option value="inherit">Inherit defaults</option><option value="balanced"
            >Balanced</option
          ><option value="quality">Quality</option><option value="speed">Speed</option></select
        ></label
      >
      <label class="toggle-field"
        ><span
          ><strong>Restrict this chat to local models</strong><small
            >A chat restriction cannot be loosened by a pin.</small
          ></span
        ><input
          type="checkbox"
          role="switch"
          checked={chat.localOnly}
          onchange={(event) => service.setLocalRestriction(event.currentTarget.checked)}
        /></label
      >
      <label class="field-label"
        >Fictional {project ? 'project monthly' : 'Temporary lifetime'} cap · USD<input
          type="number"
          aria-label="Fictional cloud cap"
          min="0"
          max="10"
          step="0.1"
          value={project?.cap ?? chat.cap}
          onchange={(event) => service.setCap(event.currentTarget.valueAsNumber)}
        /></label
      >
      <p class="panel-description">
        Used / held: ${(snapshot.usage[chat.projectId ?? chat.id] ?? 0).toFixed(2)}. Profile: ${snapshot.profileUsed.toFixed(
          2,
        )} / $25 per UTC month. Request ceiling: $1. Fixture prices and token estimates are fictional.
      </p>
      <div class="effective-route">
        <span
          >{chat.requestIntent
            ? 'One-request override'
            : chat.inheritIntent
              ? 'Inherited model intent'
              : plan.intent === 'auto'
                ? 'Auto preview'
                : 'Pinned model'}</span
        ><strong>{plan.model?.name ?? 'No eligible model'}</strong>
        <p>{plan.reason}</p>
        {#if chat.intent !== 'auto'}<button class="text-button" onclick={() => service.pin('auto')}
            >Resume Auto</button
          >{/if}
        {#if !chat.inheritIntent}<button class="text-button" onclick={() => service.pin('inherit')}
            >Reset chat model to inherit</button
          >{/if}
      </div>
      <details class="advanced-preferences">
        <summary>Request and continuation settings</summary><label class="field-label"
          >One-request model override<select
            aria-label="One-request model override"
            value={chat.requestIntent ?? ''}
            onchange={(event) => service.setRequestIntent(event.currentTarget.value)}
            ><option value="">None · use chat intent</option><option value="auto"
              >Auto for one request</option
            >{#each snapshot.models as model}<option value={model.id}>{model.name}</option
              >{/each}</select
          ></label
        >
        <p class="panel-description">
          Retained through refusal or changed-plan review; consumed only on dispatch. A new picker
          choice replaces this override.
        </p>
        <label class="field-label"
          >Chat cloud ceiling · fictional USD<input
            type="number"
            min="0"
            max="10"
            step="0.1"
            aria-label="Chat cloud ceiling"
            value={chat.cap}
            onchange={(event) => service.setChatCap(event.currentTarget.valueAsNumber)}
          /></label
        >
        <p class="panel-description">
          Effective ceiling is the lower of chat and project/session limits, under the $25 profile
          allowance. Managed profile restriction: {snapshot.profileLocalOnly
            ? 'Local-only'
            : 'none'}.
        </p>
        <label class="toggle-field"
          ><span
            ><strong>Simulate opaque model continuation</strong><small
              >Locks this conversation to {chat.continuationModel ??
                plan.model?.name ??
                'its model'}; no arbitrary state translation.</small
            ></span
          ><input
            type="checkbox"
            checked={!!chat.continuationModel}
            onchange={(event) => service.setContinuation(event.currentTarget.checked)}
          /></label
        >{#if chat.continuationModel}<button
            class="secondary-button"
            disabled={!chat.projectId}
            onclick={() => service.newConversation()}>Start a new chat without continuation</button
          >{/if}
      </details>
      <button class="panel-link" onclick={onmodels}
        >Manage local models<ArrowUpRight size={14} /></button
      ><button class="panel-link" onclick={onconnections}
        >Manage demo connections<ArrowUpRight size={14} /></button
      >
    {/if}
  </div>
</aside>
