<script lang="ts">
  import { Link, Cloud, ShieldCheck, Plus, Check, Trash2 } from '@lucide/svelte';
  import type { WorkspaceState } from '../lib/types.ts';
  import type { MockWorkspace } from '../lib/workspace.ts';
  let { snapshot, service }: { snapshot: WorkspaceState; service: MockWorkspace } = $props();
</script>

<section class="management-view" aria-label="Connections">
  <div class="page-heading">
    <span class="page-icon"><Link size={23} /></span>
    <h1>Connections</h1>
    <p>Add an optional paid model path. Each project keeps its own cloud permission.</p>
  </div>
  <div class="connection-card glass">
    <div class="connection-heading">
      <span class="model-symbol cloud"><Cloud size={24} /></span>
      <div>
        <h2>Personal cloud connection</h2>
        <p>Claude & GPT · simulated provider</p>
      </div>
      <span class:ready={snapshot.connection.status === 'verified'} class="model-state"
        >{snapshot.connection.status}</span
      >
    </div>
    {#if snapshot.connection.status === 'absent'}<p>
        No sign-in or paid connection is needed to use local models.
      </p>
      <button class="primary-button" onclick={() => service.configureConnection()}
        ><Plus size={16} />Add demo connection</button
      >
    {:else}<div class="connection-fields">
        <label class="field-label"
          >Simulated test result<select
            aria-label="Simulated connection test result"
            value={snapshot.connection.scenario}
            onchange={(event) =>
              service.setConnectionScenario(
                event.currentTarget.value as 'success' | 'invalid' | 'offline',
              )}
            ><option value="success">Verified working</option><option value="invalid"
              >Invalid credentials</option
            ><option value="offline">Offline</option></select
          ></label
        >
        <div class="connection-buttons">
          <button
            class="primary-button"
            disabled={snapshot.connection.status === 'testing'}
            onclick={() => service.testConnection()}
            ><Check size={15} />{snapshot.connection.status === 'testing'
              ? 'Testing…'
              : 'Test demo connection'}</button
          ><button class="secondary-button" onclick={() => service.removeConnection()}
            ><Trash2 size={15} />Remove</button
          >
        </div>
      </div>{/if}
    <div class="connection-disclosure">
      <ShieldCheck size={17} />
      <p>
        Connection setup does not enable cloud use. Open a project’s Preferences and explicitly
        allow cloud for its selected prompt, files and instructions. Prices and allowances are
        fictional. No keys are collected or provider calls made.
      </p>
    </div>
  </div>
</section>
