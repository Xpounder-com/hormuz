<script lang="ts">
  import { X, Plus, FileText, Trash2, ShieldCheck, ArrowUpRight } from '@lucide/svelte';
  import type { Chat, RoutePlan, WorkspaceState } from '../lib/types.ts';
  import type { MockWorkspace } from '../lib/workspace.ts';
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
    {#each ['Files', 'Instructions', 'Preferences'] as label}<button
        role="tab"
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
  <div class="context-content">
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
                  disabled={file.status !== 'available'}
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
                aria-label={`Remove ${file.name} from project`}
                title="Remove sample project copy"
                onclick={() => service.removeFile(file.id)}><Trash2 size={13} /></button
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
    {:else}
      <div class="panel-section-title">
        <h2>{project ? 'Project defaults' : 'Temporary session'}</h2>
      </div>
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
        <span>{chat.intent === 'auto' ? 'Auto preview' : 'Pinned model'}</span><strong
          >{plan.model?.name ?? 'No eligible model'}</strong
        >
        <p>{plan.reason}</p>
        {#if chat.intent !== 'auto'}<button class="text-button" onclick={() => service.pin('auto')}
            >Resume Auto</button
          >{/if}
      </div>
      <button class="panel-link" onclick={onmodels}
        >Manage local models<ArrowUpRight size={14} /></button
      ><button class="panel-link" onclick={onconnections}
        >Manage demo connections<ArrowUpRight size={14} /></button
      >
    {/if}
  </div>
</aside>
