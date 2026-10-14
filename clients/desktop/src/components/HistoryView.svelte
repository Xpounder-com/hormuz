<script lang="ts">
  import { untrack } from 'svelte';
  import { Search, Plus, Pin, Archive, Folder, ArrowUpRight, Upload } from '@lucide/svelte';
  import type { WorkspaceState } from '../lib/types.ts';
  import type { MockWorkspace } from '../lib/workspace.ts';
  import { activeChat } from '../lib/planner.ts';
  import { searchHistory } from '../lib/workflows.ts';
  let {
    snapshot,
    service,
    onopen,
    onaction,
    onsettings,
  }: {
    snapshot: WorkspaceState;
    service: MockWorkspace;
    onopen: (id: string) => void;
    onaction: (action: string) => void;
    onsettings: () => void;
  } = $props();
  let query = $state('');
  let scope = $state(untrack(() => activeChat(snapshot).projectId ?? 'profile'));
  let mode = $state<'title' | 'content'>('title');
  let archives = $state<'active' | 'archived' | 'all'>('active');
  const results = $derived(searchHistory(snapshot, query, scope, mode, archives));
  const unavailable = $derived(['unavailable', 'corrupt'].includes(snapshot.storage.status));
</script>

<section class="management-view" aria-label="Chat history">
  <div class="page-heading">
    <span class="page-icon"><Search size={23} /></span>
    <h1>Find your way back.</h1>
    <p>
      Search this profile’s saved conversations. Temporary chats and unsent drafts are excluded.
    </p>
  </div>
  <div class="history-search glass">
    <label class="field-label"
      >Search<input
        type="search"
        aria-label="Search history"
        placeholder="A title, phrase or question…"
        maxlength="200"
        bind:value={query}
      /></label
    >
    <div class="form-grid">
      <label class="field-label"
        >Where<select aria-label="History project scope" bind:value={scope}
          ><option value="profile">All projects in this profile</option
          >{#each snapshot.projects as project}<option value={project.id}
              >{project.name}{project.archived ? ' · archived' : ''}</option
            >{/each}</select
        ></label
      >
      <label class="field-label"
        >Match<select aria-label="History search mode" bind:value={mode}
          ><option value="title">Titles only</option><option value="content"
            >Titles and message content</option
          ></select
        ></label
      >
      <label class="field-label"
        >Archive<select aria-label="History archive scope" bind:value={archives}
          ><option value="active">Active work</option><option value="archived">Archived work</option
          ><option value="all">Active and archived</option></select
        ></label
      >
    </div>
    <div class="history-actions">
      <button class="secondary-button" onclick={() => onaction('import-project')}
        ><Upload size={15} />Import project</button
      ><button
        class="secondary-button"
        disabled={scope === 'profile'}
        onclick={() => {
          service.selectProject(scope);
          onaction('export-project');
        }}>Export this project</button
      ><span
        >{snapshot.profileId === 'personal' ? 'Personal' : 'Sandbox'} profile · memory simulation</span
      >
    </div>
  </div>
  {#if unavailable}<div class="state-panel" role="status">
      <h2>History is unavailable</h2>
      <p>
        The simulated store is {snapshot.storage.status}. Your in-memory work has not been deleted.
      </p>
      <button class="secondary-button" onclick={onsettings}>Recover in Settings</button>
    </div>
  {:else}<div class="history-results glass">
      <div class="panel-section-title">
        <h2>{query ? 'Search results' : 'Saved chats'}</h2>
        <span>{results.length}{results.length === 100 ? ' · first 100' : ''}</span>
      </div>
      {#each results as chat}<div class="history-row">
          <button
            class="history-open"
            aria-label={`Open chat ${chat.title}`}
            onclick={() => onopen(chat.id)}
            ><Folder size={17} /><span
              ><strong title={chat.title}>{chat.title}</strong><small
                >{snapshot.projects.find((p) => p.id === chat.projectId)?.name} · {chat.messages
                  .length} messages{chat.archived ? ' · archived' : ''}</small
              ></span
            ><ArrowUpRight size={15} /></button
          ><button
            class="icon-button"
            aria-label={`${chat.pinned ? 'Unpin' : 'Pin'} chat ${chat.title}`}
            aria-pressed={!!chat.pinned}
            onclick={() => service.pinChat(chat.id)}><Pin size={15} /></button
          ><button
            class="icon-button"
            aria-label={`${chat.archived ? 'Restore' : 'Archive'} chat ${chat.title}`}
            onclick={() => service.archiveChat(chat.id)}><Archive size={15} /></button
          >
        </div>{/each}
      {#if !results.length}<div class="state-panel">
          <h2>{query ? 'No matching conversations' : 'A clean slate.'}</h2>
          <p>
            {query
              ? 'Try another phrase, content search or a wider archive scope.'
              : 'Create a project or start a conversation. Archives preserve work until you remove it.'}
          </p>
          <button class="secondary-button" onclick={() => onaction('new-project')}
            ><Plus size={15} />New project</button
          >
        </div>{/if}
    </div>{/if}
</section>
