<script lang="ts">
  import { onMount } from 'svelte';
  import {
    PanelLeft,
    PanelRight,
    ChevronRight,
    MoreHorizontal,
    Command,
    Folder,
    X,
    ArrowUpRight,
    ShieldCheck,
    Clock3,
  } from '@lucide/svelte';
  import Sidebar from './components/Sidebar.svelte';
  import Conversation from './components/Conversation.svelte';
  import Composer from './components/Composer.svelte';
  import ContextPanel from './components/ContextPanel.svelte';
  import ModelLibrary from './components/ModelLibrary.svelte';
  import Connections from './components/Connections.svelte';
  import Settings from './components/Settings.svelte';
  import Modal from './components/Modal.svelte';
  import { MockWorkspace } from './lib/workspace.ts';
  import { activeChat, preview } from './lib/planner.ts';
  import { sampleFiles } from './lib/fixtures.ts';
  const service = new MockWorkspace();
  let snapshot = $state.raw(structuredClone(service.state));
  let view = $state('workspace');
  let compact = $state(false);
  let sidebarOpen = $state(false);
  let contextOpen = $state(false);
  let contextTab = $state('files');
  let modal = $state('');
  let name = $state('');
  let destination = $state('research');
  const chat = $derived(activeChat(snapshot));
  const project = $derived(snapshot.projects.find((p) => p.id === chat.projectId));
  const plan = $derived(preview(snapshot));
  const titles: Record<string, string> = $derived({
    'new-project': 'New project',
    'rename-project': 'Rename project',
    'rename-chat': 'Rename chat',
    'delete-project': 'Delete project?',
    'delete-chat': chat?.projectId ? 'Delete chat?' : 'Discard Temporary chat?',
    files: 'Add a sample file',
    'save-temp': 'Save Temporary chat to a project',
  });
  onMount(() => {
    const off = service.subscribe((next) => (snapshot = next));
    return () => {
      off();
      service.dispose();
    };
  });
  function navigate(next: string) {
    view = next;
    sidebarOpen = false;
    compact = false;
  }
  function showContext(tab = 'files') {
    contextTab = tab;
    contextOpen = true;
    compact = false;
    view = 'workspace';
  }
  function request(action: string) {
    modal = action;
    name =
      action === 'rename-project'
        ? (project?.name ?? '')
        : action === 'rename-chat'
          ? chat.title
          : '';
    destination = snapshot.projects[0]?.id ?? '';
  }
  function submitModal(event: SubmitEvent) {
    event.preventDefault();
    if (modal === 'new-project') {
      service.createProject(name);
      navigate('workspace');
    } else if (modal === 'rename-project' && project) service.renameProject(project.id, name);
    else if (modal === 'rename-chat') service.renameChat(chat.id, name);
    else if (modal === 'delete-project' && project) service.deleteProject(project.id);
    else if (modal === 'delete-chat') service.deleteChat(chat.id);
    else if (modal === 'save-temp') service.saveTemporary(destination);
    modal = '';
  }
  function keyboard(event: KeyboardEvent) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (!modal) compact = !compact;
    }
    if (event.key === 'Escape' && !modal && !event.defaultPrevented) {
      compact = false;
      sidebarOpen = false;
      contextOpen = false;
    }
  }
  function send() {
    service.send();
    compact = false;
    view = 'workspace';
  }
</script>

<svelte:window onkeydown={keyboard} />
<div
  class:light={snapshot.theme === 'light'}
  class:opaque={snapshot.opaque}
  class:quick-mode={compact}
  class="app-canvas"
>
  {#if compact}
    <main class="quick-stage">
      <div class="quick-heading">
        <div class="brand">
          <span class="brand-mark"><img src="/hormuz-mark.svg" alt="" /></span><strong
            >Hormuz</strong
          >
        </div>
        <span>Quick prompt</span><button
          class="icon-button"
          aria-label="Close quick bar"
          onclick={() => (compact = false)}><X size={17} /></button
        >
      </div>
      <div class="quick-shell">
        <Composer
          {snapshot}
          {chat}
          {plan}
          compact
          ondraft={(text) => service.setDraft(text)}
          onpin={(id) => service.pin(id)}
          onsend={send}
          onstop={() => service.stop()}
          oncontext={() => showContext('preferences')}
          onexpand={() => (compact = false)}
        />
        <div class="quick-destination">
          <Folder size={13} /><select
            aria-label="Quick bar destination"
            value={chat.projectId ?? 'temporary'}
            onchange={(event) =>
              event.currentTarget.value === 'temporary'
                ? service.newChat(null)
                : service.selectProject(event.currentTarget.value)}
            >{#each snapshot.projects.filter((p) => !p.archived) as item}<option value={item.id}
                >{item.name}</option
              >{/each}<option value="temporary">Temporary chat</option></select
          ><span>{chat.projectId ? 'Saved · simulated' : 'Memory only'}</span><span
            class="quick-shortcut">Esc to return</span
          >
        </div>
      </div>
      <p class="quick-disclosure">Simulation · no backend or model calls</p>
    </main>
  {:else}
    <div
      class:sidebar-open={sidebarOpen}
      class:context-open={contextOpen && view === 'workspace'}
      class="workspace-shell glass"
    >
      <Sidebar
        {snapshot}
        {view}
        onview={navigate}
        onproject={(id) => service.selectProject(id)}
        onchat={(id) => service.navigate(id)}
        onnewproject={() => request('new-project')}
        onnewchat={() => {
          service.newChat();
          navigate('workspace');
        }}
        ontemporary={() => service.newChat(null)}
        onclose={() => (sidebarOpen = false)}
      />
      {#if sidebarOpen}<button
          class="drawer-scrim"
          aria-label="Close navigation"
          onclick={() => (sidebarOpen = false)}
        ></button>{/if}
      <main class="main-workspace">
        <header class="workspace-header">
          <button
            class="icon-button mobile-menu"
            aria-label="Show sidebar"
            onclick={() => (sidebarOpen = true)}><PanelLeft size={19} /></button
          >
          <div class="breadcrumb">
            {#if view === 'workspace'}{#if project}<Folder size={15} /><span title={project.name}
                  >{project.name}</span
                >{:else}<Clock3 size={15} /><span>Temporary</span>{/if}<ChevronRight
                size={13}
              /><strong title={chat.title}>{chat.title}</strong>{:else}<strong
                >{view === 'models'
                  ? 'Model library'
                  : view === 'connections'
                    ? 'Personal connections'
                    : 'App preferences'}</strong
              >{/if}
          </div>
          <div class="header-actions">
            <button
              class="quick-launch-button"
              aria-label="Open quick bar"
              title="Quick bar · Cmd/Ctrl + K"
              onclick={() => (compact = true)}
              ><Command size={15} /><span>Quick bar</span><kbd>⌘ K</kbd></button
            >{#if view === 'workspace'}<button
                class:pressed={contextOpen}
                class="icon-button"
                aria-label="Toggle project context"
                title="Project context"
                onclick={() => (contextOpen = !contextOpen)}><PanelRight size={18} /></button
              >
              <details class="header-menu">
                <summary class="icon-button" aria-label="Workspace actions"
                  ><MoreHorizontal size={19} /></summary
                >
                <div class="actions-menu glass">
                  <button onclick={() => request('rename-chat')}>Rename chat</button><button
                    onclick={() => request('delete-chat')}
                    >{project ? 'Delete chat' : 'Discard Temporary chat'}</button
                  >{#if project}<hr />
                    <button onclick={() => request('rename-project')}>Rename project</button><button
                      onclick={() => service.archiveProject(project.id)}
                      >{project.archived ? 'Restore project' : 'Archive project'}</button
                    ><button onclick={() => request('delete-project')}>Delete project</button
                    >{:else}<button
                      disabled={!snapshot.projects.length}
                      onclick={() => request('save-temp')}>Save to project</button
                    >{/if}
                </div>
              </details>{/if}
          </div>
        </header>
        {#if snapshot.attempt && (snapshot.attempt.chatId !== chat.id || view !== 'workspace')}<div
            class="background-work"
            role="status"
          >
            <span class="busy-dot"></span>Response in {snapshot.attempt.origin}<button
              class="text-button"
              onclick={() => {
                service.navigate(snapshot.attempt!.chatId);
                navigate('workspace');
              }}>Return to chat<ArrowUpRight size={13} /></button
            >
          </div>{/if}
        {#if view === 'workspace'}
          <Conversation {chat} ondraft={(text) => service.setDraft(text)} />
          <div class="composer-region">
            {#if chat.selectedFiles.length}<button
                class="selected-context"
                onclick={() => showContext('files')}
                ><Folder size={13} />{chat.selectedFiles.length} selected {chat.selectedFiles
                  .length === 1
                  ? 'file'
                  : 'files'} · {plan.cloudAllowed ? 'Cloud allowed' : 'Local only'}</button
              >{/if}<Composer
              {snapshot}
              {chat}
              {plan}
              ondraft={(text) => service.setDraft(text)}
              onpin={(id) => service.pin(id)}
              onsend={send}
              onstop={() => service.stop()}
              oncontext={() => showContext('files')}
            />
          </div>
        {:else if view === 'models'}<ModelLibrary
            {snapshot}
            {service}
          />{:else if view === 'connections'}<Connections {snapshot} {service} />{:else}<Settings
            {snapshot}
            {service}
          />{/if}
        <footer class="workspace-status">
          <span
            ><span class="status-dot"></span>{chat.projectId
              ? 'Saved · simulated'
              : 'Temporary · memory only'}</span
          ><span><ShieldCheck size={12} />{plan.cloudAllowed ? 'Cloud allowed' : 'Local only'}</span
          ><span class="simulation-label">Simulation · no backend or model calls</span>
        </footer>
      </main>
      {#if contextOpen && view === 'workspace'}<ContextPanel
          {snapshot}
          {chat}
          {plan}
          {service}
          tab={contextTab}
          ontab={(value) => (contextTab = value)}
          onclose={() => (contextOpen = false)}
          onimport={() => request('files')}
          onmodels={() => navigate('models')}
          onconnections={() => navigate('connections')}
        />{/if}
    </div>
  {/if}
  {#if snapshot.notice}<div class="notice glass" role="status">
      <span>{snapshot.notice}</span><button
        class="icon-button"
        aria-label="Dismiss notification"
        onclick={() => service.clearNotice()}><X size={15} /></button
      >
    </div>{/if}
  {#if modal}
    <Modal title={titles[modal]} onclose={() => (modal = '')}>
      {#if modal === 'files'}<p class="modal-description">
          Choose a fixture. No real file chooser or filesystem access is used.
        </p>
        <div class="sample-file-options">
          {#each sampleFiles as file}<button
              class="sample-file-button"
              disabled={project?.files.some((f) => f.id === file.id)}
              onclick={() => {
                service.addSampleFile(file.id);
                modal = '';
              }}
              ><span
                ><strong>{file.name}</strong><small
                  >{file.status === 'available'
                    ? file.localOnly
                      ? 'Private text · Local-only'
                      : 'Supported Markdown copy'
                    : `Simulated ${file.status}`}</small
                ></span
              ><span>{project?.files.some((f) => f.id === file.id) ? 'Added' : 'Add'}</span></button
            >{/each}
        </div>
      {:else}<form onsubmit={submitModal}>
          {#if modal === 'new-project' || modal.startsWith('rename')}<label class="field-label"
              >{modal === 'rename-chat' ? 'Chat title' : 'Project name'}<input
                aria-label={modal === 'rename-chat' ? 'Chat title' : 'Project name'}
                bind:value={name}
                maxlength="100"
                required
              /></label
            >{#if modal === 'new-project'}<p class="modal-description">
                Starts with Auto and Local-only. Chats, drafts and settings stay in this simulation.
              </p>{/if}
          {:else if modal === 'save-temp'}<label class="field-label"
              >Destination project<select aria-label="Save destination" bind:value={destination}
                >{#each snapshot.projects as item}<option value={item.id}>{item.name}</option
                  >{/each}</select
              ></label
            >
            <p class="modal-description">
              Copy messages and draft; clear Temporary after acknowledgment. Reset pins, chat
              instructions and selections. Future requests use the destination’s instructions,
              objective and separate cloud permission.
            </p>
            <div class="save-preview">
              <strong>{snapshot.projects.find((p) => p.id === destination)?.name}</strong><span
                >{snapshot.projects.find((p) => p.id === destination)?.cloudAllowed
                  ? 'Cloud allowed by destination'
                  : 'Local-only destination'} · Auto</span
              >
              <p>
                {snapshot.projects.find((p) => p.id === destination)?.instructions ||
                  'App instructions only'}
              </p>
            </div>
          {:else}<p class="modal-description">
              {modal === 'delete-project'
                ? 'Remove this project’s chats, drafts, instructions and sample copies from memory. Models, connections and separate usage are retained.'
                : project
                  ? 'Remove this chat and draft. Project copies and separate usage are retained.'
                  : 'Clear the Temporary draft, conversation and cloud permission. Fictional usage and unknown holds remain.'}
            </p>{/if}
          {#if (modal.includes('delete') || modal === 'save-temp') && (modal === 'delete-project' ? snapshot.attempt?.projectId === project?.id : snapshot.attempt?.chatId === chat.id)}<p
              class="blocked-operation"
            >
              Stop the active response first.
            </p>{/if}
          <div class="modal-actions">
            <button type="button" class="secondary-button" onclick={() => (modal = '')}
              >Cancel</button
            ><button
              type="submit"
              class:danger={modal.includes('delete')}
              class="primary-button"
              disabled={modal === 'delete-project'
                ? Boolean(snapshot.attempt && snapshot.attempt.projectId === project?.id)
                : modal === 'delete-chat' || modal === 'save-temp'
                  ? snapshot.attempt?.chatId === chat.id
                  : !name.trim()}
              >{modal.includes('delete')
                ? 'Remove content'
                : modal === 'save-temp'
                  ? 'Save simulated copy'
                  : modal === 'new-project'
                    ? 'Create project'
                    : 'Save name'}</button
            >
          </div>
        </form>{/if}
    </Modal>
  {/if}
</div>
