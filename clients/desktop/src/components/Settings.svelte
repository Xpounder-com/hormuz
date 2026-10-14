<script lang="ts">
  import { Settings2, Moon, Sun, ShieldCheck } from '@lucide/svelte';
  import type { WorkspaceState } from '../lib/types.ts';
  import type { MockWorkspace } from '../lib/workspace.ts';
  let { snapshot, service }: { snapshot: WorkspaceState; service: MockWorkspace } = $props();
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
  </div>
  <div class="settings-section glass">
    <h2>App defaults</h2>
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
  <div class="management-note">
    <ShieldCheck size={19} />
    <p>
      All project content is held in browser memory. “Saved” means saved in this simulation; reload
      resets it. The browser shortcut is Cmd/Ctrl + K. An OS-wide launcher and Electron packaging
      come later.
    </p>
  </div>
</section>
