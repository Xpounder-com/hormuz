<script lang="ts">
  import { Settings2, Moon, Sun, ShieldCheck } from '@lucide/svelte';
  import type { WorkspaceState } from '../lib/types.ts';
  import type { MockWorkspace } from '../lib/workspace.ts';
  let {
    snapshot,
    service,
    onaction,
    onwindow,
  }: {
    snapshot: WorkspaceState;
    service: MockWorkspace;
    onaction: (action: string) => void;
    onwindow: (state: 'hidden' | 'closed') => void;
  } = $props();
</script>

<section class="management-view" aria-label="App settings">
  <div class="page-heading">
    <span class="page-icon"><Settings2 size={23} /></span>
    <h1>Settings</h1>
    <p>A calm workspace, with defaults you can understand.</p>
  </div>
  <div class="settings-section glass">
    <h2>Appearance</h2>
    <div class="setting-row">
      <span>Theme</span>
      <div class="segmented">
        <button
          class:selected={snapshot.theme === 'dark'}
          aria-pressed={snapshot.theme === 'dark'}
          onclick={() => service.setTheme('dark')}><Moon size={15} />Dark</button
        ><button
          class:selected={snapshot.theme === 'light'}
          aria-pressed={snapshot.theme === 'light'}
          onclick={() => service.setTheme('light')}><Sun size={15} />Light</button
        >
      </div>
    </div>
    <label class="toggle-field"
      ><span
        ><strong>Opaque surfaces</strong><small
          >Reduce transparency. System contrast and motion preferences are respected.</small
        ></span
      ><input
        type="checkbox"
        role="switch"
        checked={snapshot.opaque}
        onchange={(event) => service.setOpaque(event.currentTarget.checked)}
      /></label
    >
    <label class="toggle-field"
      ><span
        ><strong>Reduced motion</strong><small
          >Keep progress readable without decorative animation.</small
        ></span
      ><input
        type="checkbox"
        role="switch"
        checked={snapshot.reducedMotion}
        onchange={(event) => service.setAppearance('reducedMotion', event.currentTarget.checked)}
      /></label
    >
    <label class="toggle-field"
      ><span
        ><strong>Higher contrast</strong><small
          >Increase text and boundary contrast. System forced colors remain supported.</small
        ></span
      ><input
        type="checkbox"
        role="switch"
        checked={snapshot.highContrast}
        onchange={(event) => service.setAppearance('highContrast', event.currentTarget.checked)}
      /></label
    >
  </div>
  <div class="settings-section glass">
    <h2>App defaults</h2>
    <label class="field-label"
      >Default model intent<select
        aria-label="App model default"
        value={snapshot.appIntent}
        onchange={(event) => service.setAppIntent(event.currentTarget.value)}
        ><option value="auto">Auto</option>{#each snapshot.models as model}<option value={model.id}
            >{model.name}</option
          >{/each}</select
      ></label
    >
    <label class="field-label"
      >App instructions<textarea
        class="field-textarea"
        aria-label="App instructions"
        rows="3"
        maxlength="8000"
        value={snapshot.appInstructions}
        oninput={(event) => service.setAppInstructions(event.currentTarget.value)}
      ></textarea></label
    >
    <label class="field-label"
      >Objective for new projects<select
        aria-label="App objective"
        value={snapshot.appObjective}
        onchange={(event) =>
          service.setAppObjective(event.currentTarget.value as 'balanced' | 'quality' | 'speed')}
        ><option value="balanced">Balanced · local preference</option><option value="quality"
          >Quality</option
        ><option value="speed">Speed</option></select
      ></label
    >
    <p class="panel-description">
      New projects start with Auto and Local-only. Existing projects keep their settings. Telemetry
      and automatic updates are off in this simulation.
    </p>
  </div>
  <div class="settings-section glass">
    <h2>Profiles and local content</h2>
    <label class="field-label"
      >Demo profile<select
        aria-label="Demo profile"
        value={snapshot.profileId}
        onchange={(event) =>
          service.switchProfile(event.currentTarget.value as WorkspaceState['profileId'])}
        ><option value="personal">Personal · sample projects</option><option value="sandbox"
          >Sandbox · fresh first launch</option
        ></select
      ></label
    >
    <p class="panel-description">
      Profile projects, search, consent, connections and usage are isolated in memory. Active work
      and setup must stop before switching. No account sign-in.
    </p>
    <div class="history-actions">
      <button class="secondary-button" onclick={() => onaction('import-project')}
        >Import project</button
      ><button class="secondary-button" onclick={() => onaction('restart')}
        >Simulate quit / reopen</button
      >
    </div>
    <p class="panel-description">
      Named drafts save to a debounced mock journal. Temporary bodies never enter it. No durable
      store is used; browser reload resets both profiles.
    </p>
  </div>
  <div class="settings-section glass">
    <h2>Network choices</h2>
    <label class="toggle-field"
      ><span
        ><strong>Usage telemetry</strong><small
          >Off by default. This simulation sends no telemetry in either state.</small
        ></span
      ><input
        type="checkbox"
        role="switch"
        checked={snapshot.telemetry}
        onchange={(event) => service.setFixture('telemetry', event.currentTarget.checked)}
      /></label
    ><label class="toggle-field"
      ><span
        ><strong>Automatic update checks</strong><small
          >Independent of prompt privacy. No updater runs in this simulation.</small
        ></span
      ><input
        type="checkbox"
        role="switch"
        checked={snapshot.updateChecks}
        onchange={(event) => service.setFixture('updateChecks', event.currentTarget.checked)}
      /></label
    >
    <p class="panel-description">
      Catalog lookup, downloads, connection tests and updates are separate explicit actions. Typing
      only previews local fixture state.
    </p>
  </div>
  <details class="settings-section glass demo-scenarios">
    <summary>Explore recovery scenarios</summary>
    <p class="panel-description">
      These controls change fictional outcomes. They do not change your machine, account, files or
      network.
    </p>
    <label class="field-label"
      >Project storage<select
        aria-label="Simulated project storage"
        value={snapshot.storage.status}
        onchange={(event) =>
          service.setStorageScenario(
            event.currentTarget.value as WorkspaceState['storage']['status'],
          )}
        >{#each ['available', 'unavailable', 'corrupt', 'full', 'write-failed'] as status}<option
            value={status}>{status}</option
          >{/each}</select
      ></label
    >
    <p role="status" class="panel-description">
      {snapshot.storage.save} · {snapshot.storage.error || 'Memory journal only.'}
    </p>
    <button class="secondary-button" onclick={() => service.retrySave()}
      >Retry simulated save</button
    >
    <label class="field-label"
      >Model setup<select
        aria-label="Simulated model setup"
        value={snapshot.setupScenario}
        onchange={(event) =>
          service.setFixture(
            'setupScenario',
            event.currentTarget.value as WorkspaceState['setupScenario'],
          )}
        ><option value="normal">Normal</option><option value="offline"
          >Offline / interrupted download</option
        ><option value="full">Model store full</option><option value="verify-failed"
          >Verification fails</option
        ><option value="load-failed">Load fails</option></select
      ></label
    >
    <label class="field-label"
      >Model catalog<select
        aria-label="Simulated catalog"
        value={snapshot.catalog}
        onchange={(event) =>
          service.setFixture('catalog', event.currentTarget.value as WorkspaceState['catalog'])}
        ><option value="available">Available</option><option value="unavailable">Unavailable</option
        ></select
      ></label
    >
    <label class="field-label"
      >Next Send boundary<select
        aria-label="Simulated Send outcome"
        value={snapshot.sendScenario}
        onchange={(event) =>
          service.setFixture(
            'sendScenario',
            event.currentTarget.value as WorkspaceState['sendScenario'],
          )}
        ><option value="normal">Normal stream</option><option value="route-change"
          >Model becomes unavailable at Send</option
        ><option value="price-change">Upper price allowance changes at Send</option><option
          value="failed">Partial response failure</option
        ><option value="unknown">Outcome unknown after partial output</option></select
      ></label
    >
    <label class="field-label"
      >Launcher availability<select
        aria-label="Simulated launcher availability"
        value={snapshot.hostScenario}
        onchange={(event) =>
          service.setFixture(
            'hostScenario',
            event.currentTarget.value as WorkspaceState['hostScenario'],
          )}
        ><option value="normal">Available</option><option value="shortcut-collision"
          >Shortcut collision</option
        ><option value="focus-unavailable">Floating-bar focus unavailable</option><option
          value="host-error">Host unavailable</option
        ></select
      ></label
    >
    <label class="toggle-field"
      ><span
        ><strong>Managed profile Local-only ceiling</strong><small
          >Overrides all project/chat/request/skill choices.</small
        ></span
      ><input
        type="checkbox"
        checked={snapshot.profileLocalOnly}
        onchange={(event) => service.setProfileRestriction(event.currentTarget.checked)}
      /></label
    >
    <div class="history-actions">
      <button class="secondary-button" onclick={() => service.seedHistory()}
        >Add 60 sample chats</button
      ><button class="secondary-button" onclick={() => onwindow('hidden')}>Hide demo window</button
      ><button class="secondary-button" onclick={() => onwindow('closed')}>Close demo window</button
      >
    </div>
  </details>
  <div class="management-note">
    <ShieldCheck size={19} />
    <p>
      All project content is held in browser memory. “Saved” means saved in this simulation; reload
      resets it. The browser shortcut is Cmd/Ctrl + K. An OS-wide launcher and Electron packaging
      come later.
    </p>
  </div>
</section>
