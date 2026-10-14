<script lang="ts">
  import { onMount, tick } from 'svelte';
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
  import ResearchView from './components/ResearchView.svelte';
  import HistoryView from './components/HistoryView.svelte';
  import WorkspaceDialog from './components/WorkspaceDialog.svelte';
  import FileDialog from './components/FileDialog.svelte';
  import { MockResearch } from './lib/research.ts';
  import { MockWorkspace } from './lib/workspace.ts';
  import { activeChat, preview } from './lib/planner.ts';
  import { sampleFiles } from './lib/fixtures.ts';
  const service = new MockWorkspace();
  const research = new MockResearch(service);
  let snapshot = $state.raw(structuredClone(service.state));
  let researchSnapshot = $state.raw(structuredClone(research.state));
  let view = $state('workspace');
  let compact = $state(false);
  let sidebarOpen = $state(false);
  let contextOpen = $state(false);
  let contextTab = $state('files');
  let modal = $state('');
  let name = $state('');
  let destination = $state('research');
  let fileId = $state('');
  let windowState = $state<'open' | 'hidden' | 'closed'>('open');
  let sidebarCollapsed = $state(false);
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
    const offResearch = research.subscribe((next) => (researchSnapshot = next));
    return () => {
      offResearch();
      research.dispose();
      off();
      service.dispose();
    };
  });
  function navigate(next: string) {
    if (next === 'research') research.open(activeChat(service.state).id);
    view = next;
    sidebarOpen = false;
    compact = false;
    windowState = 'open';
  }
  function showContext(tab = 'files') {
    contextTab = tab;
    contextOpen = true;
    compact = false;
    view = 'workspace';
  }
  function request(action: string) {
    const menu = document.querySelector<HTMLDetailsElement>('.header-menu[open]');
    menu?.querySelector<HTMLElement>('summary')?.focus();
    menu?.removeAttribute('open');
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
    else if (modal === 'save-temp') {
      service.saveTemporary(destination);
      if (activeChat(service.state).projectId === null) return;
    }
    modal = '';
  }
  function keyboard(event: KeyboardEvent) {
    if (event.isComposing) return;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (!modal && !snapshot.sendConfirmation) {
        if (compact) closeQuick();
        else openQuick(true);
      }
    }
    if (
      (event.metaKey || event.ctrlKey) &&
      event.shiftKey &&
      event.key.toLowerCase() === 'f' &&
      !modal &&
      !snapshot.sendConfirmation
    ) {
      event.preventDefault();
      navigate('history');
    }
    if (event.key === 'Escape' && !modal && !event.defaultPrevented) {
      if (compact) closeQuick();
      sidebarOpen = false;
      contextOpen = false;
    }
  }
  function send() {
    service.send(plan.fingerprint);
    compact = false;
    view = 'workspace';
  }
  function openQuick(shortcut = false) {
    if (
      (shortcut && snapshot.hostScenario === 'shortcut-collision') ||
      snapshot.hostScenario === 'focus-unavailable' ||
      snapshot.hostScenario === 'host-error'
    ) {
      navigate('workspace');
      service.notify(
        'The simulated launcher is unavailable. Use the ordinary window and composer; your draft and destination are retained.',
      );
      return;
    }
    windowState = 'open';
    compact = true;
  }
  async function closeQuick() {
    compact = false;
    await tick();
    document.querySelector<HTMLButtonElement>('[aria-label="Open quick bar"]')?.focus();
  }
</script>

<svelte:window onkeydown={keyboard} />
<div
  class:light={snapshot.theme === 'light'}
  class:opaque={snapshot.opaque}
  class:quick-mode={compact}
  class:reduced-motion={snapshot.reducedMotion}
  class:high-contrast={snapshot.highContrast}
  class="app-canvas"
>
  {#if windowState !== 'open'}<main class="closed-window glass">
      <span class="brand-mark"><img src="/hormuz-mark.svg" alt="" /></span>
      <h1>Workspace {windowState === 'hidden' ? 'hidden' : 'closed'}.</h1>
      <p>
        The application simulation stays alive. Drafts, Temporary work and owned operations keep
        their original destination.
      </p>
      {#if snapshot.attempt || snapshot.reviewActivity}<p role="status">
          Work is still active in its originating chat.
        </p>{/if}
      <div class="history-actions">
        <button class="primary-button" onclick={() => (windowState = 'open')}
          >Reopen workspace</button
        ><button class="secondary-button" onclick={() => openQuick()}>Open quick bar</button><button
          class="secondary-button"
          onclick={() => {
            windowState = 'open';
            request('restart');
          }}>Quit / reopen demo…</button
        >
      </div>
      <p class="panel-description">This does not control an actual Electron window or tray.</p>
    </main>
  {:else if compact}
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
          onclick={closeQuick}><X size={17} /></button
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
          ><span
            >{chat.projectId
              ? `${snapshot.storage.save === 'failed' ? 'Save failed' : snapshot.storage.save === 'saving' ? 'Saving' : 'Saved'} · simulated`
              : 'Memory only'}</span
          ><span class="quick-shortcut">Esc to return</span>
        </div>
      </div>
      <p class="quick-disclosure">Simulation · no backend or model calls</p>
    </main>
  {:else}
    <div
      class:sidebar-open={sidebarOpen}
      class:context-open={contextOpen && view === 'workspace'}
      class:sidebar-collapsed={sidebarCollapsed}
      class="workspace-shell glass"
    >
      {#if !sidebarCollapsed}<Sidebar
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
        />{/if}
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
            onclick={() => {
              sidebarCollapsed = false;
              sidebarOpen = true;
            }}><PanelLeft size={19} /></button
          >
          <button
            class="icon-button desktop-sidebar-toggle"
            aria-label={sidebarCollapsed ? 'Show sidebar' : 'Collapse sidebar'}
            title={sidebarCollapsed ? 'Show sidebar' : 'Collapse sidebar'}
            onclick={() => (sidebarCollapsed = !sidebarCollapsed)}><PanelLeft size={17} /></button
          >
          <div class="breadcrumb">
            {#if view === 'workspace' || view === 'research'}{#if project}<Folder size={15} /><span
                  title={project.name}>{project.name}</span
                >{:else}<Clock3 size={15} /><span>Temporary</span>{/if}<ChevronRight
                size={13}
              /><strong title={chat.title}
                >{chat.title}{view === 'research' ? ' · Research' : ''}</strong
              >{:else}<strong
                >{view === 'history'
                  ? 'Chat history'
                  : view === 'models'
                    ? 'Model library'
                    : view === 'research'
                      ? 'Research workspace'
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
              onclick={() => openQuick()}
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
                    <button onclick={() => service.pinChat(chat.id)}
                      >{chat.pinned ? 'Unpin chat' : 'Pin chat'}</button
                    ><button onclick={() => service.archiveChat(chat.id)}
                      >{chat.archived ? 'Restore chat' : 'Archive chat'}</button
                    ><button
                      disabled={snapshot.projects.length < 2}
                      onclick={() => request('move-chat')}>Move chat…</button
                    ><button onclick={() => request('clear-chat')}>Clear conversation…</button
                    ><button onclick={() => navigate('history')}>Close chat view</button>
                    <hr />
                    <button onclick={() => request('rename-project')}>Rename project</button><button
                      onclick={() => service.archiveProject(project.id)}
                      >{project.archived ? 'Restore project' : 'Archive project'}</button
                    ><button onclick={() => request('delete-project')}>Delete project</button
                    ><button onclick={() => service.pinProject(project.id)}
                      >{project.pinned ? 'Unpin project' : 'Pin project'}</button
                    ><button onclick={() => request('export-project')}>Export project…</button
                    ><button onclick={() => request('import-project')}>Import project…</button
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
        {#if snapshot.reviewActivity && (view !== 'research' || snapshot.reviewActivity.chatId !== chat.id)}
          <div class="background-work" role="status">
            <span class="busy-dot"></span>Research review in {snapshot.reviewActivity.origin}<button
              class="text-button"
              onclick={() => {
                service.navigate(snapshot.reviewActivity!.chatId);
                navigate('research');
              }}>Return to review<ArrowUpRight size={13} /></button
            ><button class="text-button" onclick={() => research.stop()}>Stop</button>
          </div>
        {/if}
        {#if snapshot.storage.save === 'failed' && chat.projectId}<div
            class="background-work recovery-banner"
            role="status"
          >
            <span>Save failed · work remains in memory.</span><button
              class="text-button"
              onclick={() => navigate('settings')}>Recover</button
            ><button class="text-button" onclick={() => request('export-project')}
              >Copy an export</button
            >
          </div>{/if}
        {#if chat.archived && view === 'workspace'}<div class="background-work" role="status">
            Archived chat · retained locally<button
              class="text-button"
              onclick={() => service.archiveChat(chat.id)}>Restore chat</button
            >
          </div>{/if}
        {#if view === 'workspace'}
          {#if snapshot.firstRun || !snapshot.projects.length}<section
              class="onboarding glass"
              aria-label="Local-first onboarding"
            >
              <h1>Your work, with a home.</h1>
              <p>
                Create a project before choosing a model. No account or hosted service is needed.
                This demo saves to memory; real local persistence comes later.
              </p>
              <div class="history-actions">
                <button class="primary-button" onclick={() => request('new-project')}
                  >Create your first project</button
                ><button class="secondary-button" onclick={() => navigate('models')}
                  >Explore local setup</button
                ><button class="secondary-button" onclick={() => navigate('connections')}
                  >Optional paid connection</button
                >{#if snapshot.projects.length}<button
                    class="text-button"
                    onclick={() => service.dismissOnboarding()}>Continue to workspace</button
                  >{/if}
              </div>
            </section>{/if}
          <Conversation
            {chat}
            ondraft={(text) => service.setDraft(text)}
            onretry={(id) => service.retryResponse(id)}
          />
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
        {:else if view === 'history'}<HistoryView
            {snapshot}
            {service}
            onopen={(id) => {
              service.navigate(id);
              navigate('workspace');
            }}
            onaction={request}
            onsettings={() => navigate('settings')}
          />
        {:else if view === 'research'}<ResearchView
            {snapshot}
            {researchSnapshot}
            session={researchSnapshot.sessions[chat.id]}
            service={research}
            oncontext={() => showContext('files')}
          />
        {:else if view === 'models'}<ModelLibrary
            {snapshot}
            {service}
          />{:else if view === 'connections'}<Connections {snapshot} {service} />{:else}<Settings
            {snapshot}
            {service}
            onaction={request}
            onwindow={(value) => (windowState = value)}
          />{/if}
        <footer class="workspace-status">
          <span
            ><span
              class:failed={!!chat.projectId && snapshot.storage.save === 'failed'}
              class:saving={!!chat.projectId && snapshot.storage.save === 'saving'}
              class="status-dot"
            ></span>{chat.projectId
              ? `${snapshot.storage.save === 'failed' ? 'Save failed' : snapshot.storage.save === 'saving' ? 'Saving' : 'Saved'} · simulated`
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
          onfile={(id) => {
            fileId = id;
            modal = 'file-preview';
          }}
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
  {#if snapshot.sendConfirmation}<Modal
      title="Review the changed Send plan"
      onclose={() => service.cancelSendConfirmation()}
      ><p class="modal-description">
        Nothing was dispatched. Your draft and one-request override are retained. The new proposal
        needs renewed intent.
      </p>
      <div class="save-preview">
        <strong>Previously visible</strong>
        <p>{snapshot.sendConfirmation.before}</p>
        <strong>Current proposal</strong>
        <p>{snapshot.sendConfirmation.after}</p>
        <p>
          Included: {plan.context.map((f) => `${f.name} v${f.revision}`).join(', ') || 'no files'}. {plan.cloudAllowed
            ? 'Selected context may go to the proposed paid model.'
            : 'Effective Local-only.'}
        </p>
      </div>
      <div class="modal-actions">
        <button class="secondary-button" onclick={() => service.cancelSendConfirmation()}
          >Return to draft</button
        ><button
          class="primary-button"
          disabled={plan.blocked}
          onclick={() => {
            service.navigate(snapshot.sendConfirmation!.chatId);
            service.send(snapshot.sendConfirmation!.fingerprint, true);
            compact = false;
            view = 'workspace';
          }}>Send revised plan</button
        >
      </div></Modal
    >{/if}
  {#if modal === 'file-preview'}<FileDialog
      {snapshot}
      {service}
      id={fileId}
      onclose={() => (modal = '')}
    />
  {:else if ['move-chat', 'export-project', 'import-project', 'clear-chat', 'restart'].includes(modal)}<WorkspaceDialog
      action={modal}
      {snapshot}
      {service}
      onclose={() => (modal = '')}
      oncomplete={() => {
        modal = '';
        navigate('workspace');
      }}
      onstopreview={() => research.stop()}
    />
  {:else if modal}
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
              Copy messages, draft and research history; clear Temporary after acknowledgment. Reset
              pins, chat instructions and selections. Future requests use the destination’s
              instructions, objective and separate cloud permission.
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
          {#if (modal.includes('delete') || modal === 'save-temp') && (modal === 'delete-project' ? snapshot.attempt?.projectId === project?.id || snapshot.reviewActivity?.projectId === project?.id : snapshot.attempt?.chatId === chat.id || snapshot.reviewActivity?.chatId === chat.id)}<p
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
                ? Boolean(
                    (snapshot.attempt && snapshot.attempt.projectId === project?.id) ||
                    (snapshot.reviewActivity && snapshot.reviewActivity.projectId === project?.id),
                  )
                : modal === 'delete-chat' || modal === 'save-temp'
                  ? snapshot.attempt?.chatId === chat.id ||
                    snapshot.reviewActivity?.chatId === chat.id
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
