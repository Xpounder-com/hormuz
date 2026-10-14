<script lang="ts">
  import { untrack } from 'svelte';
  import { Copy, Check, ArrowRight, ShieldCheck } from '@lucide/svelte';
  import Modal from './Modal.svelte';
  import type { WorkspaceState } from '../lib/types.ts';
  import type { MockWorkspace } from '../lib/workspace.ts';
  import { activeChat } from '../lib/planner.ts';
  import { exportProject, inspectImport, movePreview } from '../lib/workflows.ts';
  let {
    action,
    snapshot,
    service,
    onclose,
    oncomplete,
  }: {
    action: string;
    snapshot: WorkspaceState;
    service: MockWorkspace;
    onclose: () => void;
    oncomplete: () => void;
  } = $props();
  const chat = $derived(activeChat(snapshot));
  const project = $derived(snapshot.projects.find((p) => p.id === chat.projectId));
  let destination = $state(
    untrack(() => snapshot.projects.find((p) => p.id !== chat.projectId)?.id ?? ''),
  );
  let carry = $state(false);
  let imported = $state('');
  let partial = $state(false);
  let copied = $state(false);
  let error = $state('');
  let discardUnsaved = $state(false);
  const move = $derived(movePreview(snapshot, chat.id, destination));
  const importPreview = $derived(inspectImport(imported, snapshot));
  const exported = $derived(project ? exportProject(snapshot, project.id) : '');
  const busy = $derived(
    snapshot.attempt?.chatId === chat.id || snapshot.externalActivity?.chatId === chat.id,
  );
  const temporary = $derived(
    snapshot.chats.find(
      (c) =>
        c.projectId === null &&
        (c.draft || c.messages.length || snapshot.externalActivity?.chatId === c.id),
    ),
  );
  const titles: Record<string, string> = {
    'move-chat': 'Move this chat',
    'export-project': 'Export project · preview',
    'import-project': 'Import a project',
    'clear-chat': 'Clear this conversation?',
    restart: 'Simulate quit and reopen',
  };
  async function copy() {
    try {
      await navigator.clipboard.writeText(exported);
      copied = true;
      error = '';
    } catch {
      error = 'Clipboard unavailable. Select the JSON and copy it.';
    }
  }
  function importIt() {
    if (service.importProject(imported, partial)) oncomplete();
    else error = service.state.notice;
  }
  function restart(save = false) {
    service.stop();
    if (save && temporary) {
      service.navigate(temporary.id);
      service.saveTemporary(destination);
      if (activeChat(service.state).projectId === null) {
        error = service.state.notice;
        return;
      }
    }
    if (service.restartMock(discardUnsaved, true)) oncomplete();
    else error = service.state.notice;
  }
</script>

<Modal title={titles[action] ?? 'Workspace action'} {onclose}>
  {#if action === 'move-chat'}
    <p class="modal-description">
      Conversation, draft and earlier receipts move together. Submitted origins stay unchanged.
    </p>
    <label class="field-label"
      >Destination<select aria-label="Move destination" bind:value={destination}
        >{#each snapshot.projects.filter((p) => p.id !== chat.projectId) as item}<option
            value={item.id}>{item.name}{item.archived ? ' · archived' : ''}</option
          >{/each}</select
      ></label
    >
    {#if move}<div class="save-preview">
        <strong>{move.target.name}</strong><span
          >{move.localOnly || move.target.localOnly || !move.target.cloudAllowed
            ? 'Local-only effective privacy'
            : 'Destination cloud permission applies'} · {move.target.modelIntent ?? 'Auto'} · {move
            .target.objective}</span
        >
        <p>{move.target.instructions || 'App instructions only.'}</p>
        <p>
          Reset chat instructions, objective, model pin and one-request intent. Clear selected
          context; source copies stay with their project.
        </p>
      </div>
      <label class="toggle-field"
        ><span
          ><strong>Copy {move.copies.length} selected project files too</strong><small
            >New app-owned copies, unselected at the destination. External originals are never
            opened.</small
          ></span
        ><input type="checkbox" bind:checked={carry} /></label
      >{/if}
    {#if busy}<p class="blocked-operation">Stop the originating work before moving.</p>{/if}
    <div class="modal-actions">
      <button class="secondary-button" onclick={onclose}>Cancel</button><button
        class="primary-button"
        disabled={!move || move.busy}
        onclick={() => {
          if (service.moveChat(chat.id, destination, carry)) oncomplete();
          else error = service.state.notice;
        }}>Move chat<ArrowRight size={15} /></button
      >
    </div>
  {:else if action === 'export-project'}
    <p class="modal-description">
      {project?.name}: {snapshot.chats.filter((c) => c.projectId === project?.id).length} chats and drafts,
      {project?.files.length ?? 0} project copies, instructions and portable preferences. Historical receipts
      retain their original provenance.
    </p>
    <div class="context-summary">
      <ShieldCheck size={18} />
      <p>
        Excludes Temporary work, skills selections, other profiles, credentials, connection
        bindings, model weights, runtime handles and execution authority. This version only
        previews/copies a simulation bundle.
      </p>
    </div>
    <textarea
      class="field-textarea export-text"
      aria-label="Project export JSON"
      readonly
      value={exported}
      rows="8"></textarea>
    <div class="modal-actions">
      <button class="secondary-button" onclick={onclose}>Close</button><button
        class="primary-button"
        disabled={!exported || new TextEncoder().encode(exported).length > 1024 * 1024}
        onclick={copy}
        >{#if copied}<Check size={15} />Copied{:else}<Copy size={15} />Copy export{/if}</button
      >
    </div>
    {#if new TextEncoder().encode(exported).length > 1024 * 1024}<p class="blocked-operation">
        This export exceeds the sample 1 MiB round-trip limit. Reduce or split its content first.
      </p>{/if}
  {:else if action === 'import-project'}
    <p class="modal-description">
      Paste a simulation export. Import creates a new project and new local identities; it never
      overwrites existing work. Do not paste credentials.
    </p>
    <textarea
      class="field-textarea export-text"
      aria-label="Project import JSON"
      bind:value={imported}
      rows="7"
      maxlength="1100000"
      placeholder="Paste the JSON from Export project…"></textarea>
    {#if imported}{#if importPreview.error}<p class="blocked-operation" role="status">
          {importPreview.error}
        </p>{:else}<div class="save-preview">
          <strong>{importPreview.name}</strong><span
            >{importPreview.bundle?.chats.length} chats · {importPreview.bundle?.files.length} supported
            copies</span
          >
          <p>
            {importPreview.conflict
              ? 'Name conflict: a new copy is proposed. Existing project stays unchanged.'
              : 'New project. No existing content replaced.'}
          </p>
          <p>
            Cloud off. Model intent unbound. No files selected. Rebind against this profile’s
            inventory before Send.
          </p>
        </div>
        {#if importPreview.warnings.length}<ul class="import-warnings">
            {#each importPreview.warnings as warning}<li>{warning}</li>{/each}
          </ul>
          <label class="toggle-field"
            ><span
              ><strong>Import the supported entries only</strong><small
                >The listed entries will be skipped. Nothing is read from external originals.</small
              ></span
            ><input type="checkbox" bind:checked={partial} /></label
          >{/if}{/if}{/if}
    <div class="modal-actions">
      <button class="secondary-button" onclick={onclose}>Cancel</button><button
        class="primary-button"
        disabled={!importPreview.bundle || (!!importPreview.warnings.length && !partial)}
        onclick={importIt}>Import new copy</button
      >
    </div>
  {:else if action === 'clear-chat'}<p class="modal-description">
      Remove this chat’s messages and opaque continuation. Keep its draft, selected context, project
      copies, preferences and separate usage. Clearing is different from deleting a chat or
      archiving it.
    </p>
    <div class="modal-actions">
      <button class="secondary-button" onclick={onclose}>Cancel</button><button
        class="primary-button danger"
        disabled={busy}
        onclick={() => {
          service.clearConversation(chat.id);
          oncomplete();
        }}>Clear messages</button
      >
    </div>
    {#if busy}<p class="blocked-operation">Stop active work first.</p>{/if}
  {:else}
    <p class="modal-description">
      This restarts only the memory simulation. Named work returns from its mock journal; no request
      resumes. Temporary bodies and permission are cleared. Unknown usage holds remain. Browser
      reload still resets everything.
    </p>
    {#if snapshot.attempt || snapshot.externalActivity}<p class="blocked-operation">
        This action will Stop owned work first, retain terminal/unknown receipts, then reopen. It
        does not guarantee provider-side cancellation.
      </p>{/if}
    {#if snapshot.storage.save === 'failed'}<p class="blocked-operation">
        There is unsaved memory. Recover storage or copy an export before leaving.
      </p>
      <label class="toggle-field"
        ><span
          ><strong>Discard unsaved memory after reviewing recovery</strong><small
            >Only the last mock journal will return.</small
          ></span
        ><input type="checkbox" bind:checked={discardUnsaved} /></label
      >{/if}
    {#if temporary}<label class="field-label"
        >Save Temporary to<select aria-label="Quit save destination" bind:value={destination}
          >{#each snapshot.projects as item}<option value={item.id}>{item.name}</option
            >{/each}</select
        ></label
      >
      <p class="modal-description">
        A Temporary chat also has unsaved work. Save resets its route/instruction/context intent to
        the destination. Its cloud consent must be granted separately.
      </p>{/if}
    <div class="modal-actions">
      <button class="secondary-button" onclick={onclose}>Cancel</button
      >{#if temporary && snapshot.projects.length}<button
          class="secondary-button"
          disabled={!destination || snapshot.storage.status !== 'available'}
          onclick={() => restart(true)}>Save Temporary and reopen</button
        >{/if}<button
        class="primary-button"
        disabled={snapshot.storage.save === 'failed' && !discardUnsaved}
        onclick={() => restart()}
        >{temporary ? 'Discard Temporary and reopen' : 'Quit and reopen demo'}</button
      >
    </div>
  {/if}
  {#if error}<p class="blocked-operation" role="status">{error}</p>{/if}
</Modal>
