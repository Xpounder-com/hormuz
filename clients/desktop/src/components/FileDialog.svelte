<script lang="ts">
  import { untrack } from 'svelte';
  import Modal from './Modal.svelte';
  import type { WorkspaceState, ProjectFile } from '../lib/types.ts';
  import type { MockWorkspace } from '../lib/workspace.ts';
  import { activeChat } from '../lib/planner.ts';
  let {
    snapshot,
    service,
    id,
    onclose,
  }: { snapshot: WorkspaceState; service: MockWorkspace; id: string; onclose: () => void } =
    $props();
  const file = $derived(
    snapshot.projects
      .find((p) => p.id === activeChat(snapshot).projectId)
      ?.files.find((f) => f.id === id),
  );
  let text = $state(untrack(() => file?.text ?? ''));
  let remove = $state(false);
</script>

<Modal title={file?.name ?? 'Project copy'} {onclose}>
  {#if file}{#if remove}<p class="modal-description">
        Remove {file.ownership === 'unbound-link'
          ? 'this unbound project entry'
          : 'this app-owned sample copy'} and deselect it in every chat in this project. Earlier submitted
        snapshots stay unchanged. External originals are never removed.
      </p>
      <div class="modal-actions">
        <button class="secondary-button" onclick={() => (remove = false)}>Back</button><button
          class="primary-button danger"
          onclick={() => {
            service.removeFile(id);
            onclose();
          }}>Remove project entry</button
        >
      </div>
    {:else}<p class="modal-description">
        {file.ownership === 'unbound-link'
          ? 'Unbound external link. No permission or content is restored; import a supported copy instead.'
          : `App-owned imported copy · v${file.revision}. Replacing creates a new revision; external originals never change.`}
      </p>
      <label class="field-label"
        >Simulated copy state<select
          aria-label="File state"
          value={file.status}
          onchange={(event) =>
            service.setFileStatus(id, event.currentTarget.value as ProjectFile['status'])}
          >{#each ['available', 'changed', 'missing', 'denied', 'read-failed', 'oversized', 'unknown', 'unsupported'] as status}<option
              value={status}>{status}</option
            >{/each}</select
        ></label
      >
      {#if file.ownership !== 'unbound-link' && /\.(md|txt)$/i.test(file.name)}<label
          class="field-label"
          >Preview / replacement<textarea
            class="field-textarea"
            aria-label="File preview text"
            bind:value={text}
            rows="8"
            maxlength="262144"></textarea></label
        >
        <p class="panel-description">
          UTF-8 .txt/.md · 256 KiB per copy, 20 copies / 1 MiB per packet, then separate 6,144-token
          sample input limit. Unknown estimates block; no silent truncation.
        </p>{:else}<p class="blocked-operation">
          This format or link is unqualified. Folder selection never implies recursive reading,
          indexing, writing or execution.
        </p>{/if}
      <div class="modal-actions">
        <button class="secondary-button" onclick={() => (remove = true)}>Remove entry…</button
        ><button class="secondary-button" onclick={onclose}>Close</button
        >{#if file.ownership !== 'unbound-link' && /\.(md|txt)$/i.test(file.name)}<button
            class="primary-button"
            onclick={() => {
              if (service.replaceFile(id, text)) onclose();
            }}>Replace copy</button
          >{/if}
      </div>
    {/if}{/if}
</Modal>
