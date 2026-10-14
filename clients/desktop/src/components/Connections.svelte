<script lang="ts">
  import { Link, Cloud, ShieldCheck, Plus, Check, Trash2 } from '@lucide/svelte';
  import type { WorkspaceState } from '../lib/types.ts';
  import type { ConnectionScenario } from '../lib/types.ts';
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
        <h2>{snapshot.connection.label ?? 'Personal cloud connection'}</h2>
        <p>
          {snapshot.connection.provider === 'claude'
            ? 'Claude API fixture'
            : snapshot.connection.provider === 'gpt'
              ? 'OpenAI API fixture'
              : 'Claude & GPT · combined simulated binding'}
        </p>
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
              service.setConnectionScenario(event.currentTarget.value as ConnectionScenario)}
            ><option value="success">Verified working</option><option value="invalid"
              >Invalid credentials</option
            ><option value="offline">Offline</option><option value="expired"
              >Expired credential</option
            ><option value="locked">Secure store locked / unavailable</option><option
              value="rate-limited">Rate limited</option
            ><option value="provider-error">Provider error</option></select
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
    {#if snapshot.connection.status !== 'absent'}<details class="advanced-preferences">
        <summary>Edit demo binding</summary><label class="field-label"
          >Provider<select
            aria-label="Demo provider binding"
            value={snapshot.connection.provider ?? 'demo'}
            onchange={(event) =>
              service.connectionDetails(
                snapshot.connection.label ?? 'Personal cloud connection',
                event.currentTarget.value as 'demo' | 'claude' | 'gpt',
              )}
            ><option value="demo">Combined fixture · not a real provider</option><option
              value="claude">Claude API fixture</option
            ><option value="gpt">OpenAI API fixture</option></select
          ></label
        ><label class="field-label"
          >Display name<input
            aria-label="Demo connection name"
            value={snapshot.connection.label ?? 'Personal cloud connection'}
            maxlength="60"
            onchange={(event) =>
              service.connectionDetails(
                event.currentTarget.value,
                snapshot.connection.provider ?? 'demo',
              )}
          /></label
        >
        <p class="panel-description">
          Editing invalidates verification; existing intent and usage remain. Paid chat
          subscriptions do not establish API access. No actual key is collected.
        </p>
      </details>{/if}
    <div class="connection-disclosure">
      <ShieldCheck size={17} />
      <p>
        Connection setup does not enable cloud use. Open a project’s Preferences and explicitly
        allow cloud for its selected prompt, files and instructions. Prices and allowances are
        fictional. No keys are collected or provider calls made. A real connection test can make a
        provider request and incur cost; this demo test only changes a fixture.
      </p>
    </div>
  </div>
</section>
