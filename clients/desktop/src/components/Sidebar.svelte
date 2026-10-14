<script lang="ts">
  import {
    Plus,
    Search,
    MessageSquare,
    Folder,
    Monitor,
    Link,
    Settings2,
    PanelLeftClose,
    Clock3,
    Archive,
    FlaskConical,
  } from '@lucide/svelte';
  import type { WorkspaceState } from '../lib/types.ts';
  let {
    snapshot,
    view,
    onview,
    onproject,
    onchat,
    onnewproject,
    onnewchat,
    ontemporary,
    onclose,
  }: {
    snapshot: WorkspaceState;
    view: string;
    onview: (view: string) => void;
    onproject: (id: string) => void;
    onchat: (id: string) => void;
    onnewproject: () => void;
    onnewchat: () => void;
    ontemporary: () => void;
    onclose: () => void;
  } = $props();
  let query = $state('');
  let showArchive = $state(false);
  const chat = $derived(snapshot.chats.find((c) => c.id === snapshot.activeChatId)!);
  const projects = $derived(snapshot.projects.filter((p) => p.archived === showArchive));
  const chats = $derived(
    snapshot.chats
      .filter(
        (c) =>
          c.projectId === chat.projectId && c.title.toLowerCase().includes(query.toLowerCase()),
      )
      .slice(0, 50),
  );
</script>

<aside class="sidebar">
  <div class="brand-row">
    <div class="brand">
      <span class="brand-mark"><img src="/hormuz-mark.svg" alt="" /></span><strong>Hormuz</strong>
    </div>
    <button class="icon-button sidebar-hide" aria-label="Hide sidebar" onclick={onclose}
      ><PanelLeftClose size={17} /></button
    >
  </div>
  <label class="sidebar-search"
    ><Search size={15} /><input
      type="search"
      aria-label="Search chat titles in this project"
      placeholder="Search chats"
      bind:value={query}
    /><kbd>⌕</kbd></label
  >
  <button class="new-chat" onclick={onnewchat}><Plus size={17} />New chat</button>
  <div class="sidebar-section-heading">
    <span>{showArchive ? 'Archived projects' : 'Projects'}</span>
    <div>
      <button
        class="icon-button"
        aria-label={showArchive ? 'Show active projects' : 'Show archived projects'}
        title="Toggle archived projects"
        onclick={() => (showArchive = !showArchive)}><Archive size={14} /></button
      ><button
        class="icon-button"
        aria-label="New project"
        title="New project"
        onclick={onnewproject}><Plus size={15} /></button
      >
    </div>
  </div>
  <nav class="project-list" aria-label="Projects">
    {#each projects as project}<button
        class:active={chat.projectId === project.id &&
          (view === 'workspace' || view === 'research')}
        class="nav-row"
        title={project.name}
        onclick={() => {
          onproject(project.id);
          onview('workspace');
        }}
        ><Folder size={17} style={`color:${project.color}`} /><span>{project.name}</span><span
          class="row-count">{snapshot.chats.filter((c) => c.projectId === project.id).length}</span
        ></button
      >{/each}
    {#if !projects.length}<p class="sidebar-empty">
        {showArchive ? 'No archived projects' : 'Create a project to get started'}
      </p>{/if}
    <button
      class:active={chat.projectId === null && (view === 'workspace' || view === 'research')}
      class="nav-row temporary-row"
      onclick={() => {
        ontemporary();
        onview('workspace');
      }}><Clock3 size={17} /><span>Temporary chat</span></button
    >
  </nav>
  <div class="sidebar-section-heading chat-heading">
    <span>{query ? 'Title results' : 'Chats'}</span><span>{chats.length}</span>
  </div>
  <nav class="chat-list" aria-label="Chats">
    {#each chats as item}<button
        class:active={item.id === chat.id && view === 'workspace'}
        class="nav-row chat-row"
        title={item.title}
        onclick={() => {
          onchat(item.id);
          onview('workspace');
        }}
        ><MessageSquare size={15} /><span>{item.title}</span
        >{#if snapshot.attempt?.chatId === item.id}<span class="busy-dot"></span>{/if}</button
      >{/each}
    {#if !chats.length}<p class="sidebar-empty">No matching chat titles</p>{/if}
  </nav>
  <nav class="utility-nav" aria-label="App navigation">
    <button class:active={view === 'research'} class="nav-row" onclick={() => onview('research')}
      ><FlaskConical size={17} /><span>Research</span></button
    >
    <button class:active={view === 'models'} class="nav-row" onclick={() => onview('models')}
      ><Monitor size={17} /><span>Models</span><span class="ready-indicator"
        >{snapshot.models.filter((m) => m.place === 'local' && m.state === 'ready').length}</span
      ></button
    >
    <button
      class:active={view === 'connections'}
      class="nav-row"
      onclick={() => onview('connections')}><Link size={17} /><span>Connections</span></button
    >
    <button class:active={view === 'settings'} class="nav-row" onclick={() => onview('settings')}
      ><Settings2 size={17} /><span>Settings</span></button
    >
  </nav>
  <div class="sidebar-footer">
    <span class="profile-avatar">M</span>
    <div><strong>Personal workspace</strong><small>Simulation · resets on reload</small></div>
  </div>
</aside>
