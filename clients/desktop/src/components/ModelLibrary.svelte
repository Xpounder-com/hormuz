<script lang="ts">
  import {
    Monitor,
    Cloud,
    Download,
    Play,
    Square,
    Trash2,
    Check,
    X,
    ShieldCheck,
  } from '@lucide/svelte';
  import type { WorkspaceState } from '../lib/types.ts';
  import type { MockWorkspace } from '../lib/workspace.ts';
  let { snapshot, service }: { snapshot: WorkspaceState; service: MockWorkspace } = $props();
  let filter = $state('all');
  const models = $derived(snapshot.models.filter((m) => filter === 'all' || m.place === filter));
</script>

<section class="management-view" aria-label="Model library">
  <div class="page-heading">
    <span class="page-icon"><Monitor size={23} /></span>
    <h1>Models</h1>
    <p>One local model loaded at a time. Auto can load a downloaded model on Send.</p>
  </div>
  <div class="segmented" aria-label="Filter models">
    {#each [['all', 'All models'], ['local', 'Local'], ['cloud', 'Cloud']] as [value, label]}<button
        class:selected={filter === value}
        aria-pressed={filter === value}
        onclick={() => (filter = value)}>{label}</button
      >{/each}
  </div>
  <div class="model-library glass">
    {#each models as model}
      <div class="library-row">
        <span class:cloud={model.place === 'cloud'} class="model-symbol"
          >{#if model.place === 'local'}<Monitor size={21} />{:else}<Cloud size={21} />{/if}</span
        >
        <div class="model-info">
          <strong>{model.name}</strong><span>{model.detail}</span><small
            >{model.size} · {model.place === 'local'
              ? 'llama.cpp simulation'
              : 'Demo provider'}</small
          >{#if ['downloading', 'verifying', 'loading'].includes(model.state)}<progress
              value={model.progress}
              max="100"
              aria-label={`${model.name} setup progress`}
            ></progress>{/if}
        </div>
        <span class:ready={model.state === 'ready' && model.fit} class="model-state"
          >{!model.fit
            ? 'Fit unavailable'
            : model.place === 'cloud'
              ? snapshot.connection.status === 'verified'
                ? 'Demo verified'
                : 'Needs connection'
              : model.state === 'ready'
                ? 'Ready · simulated'
                : model.state}</span
        >
        <div class="model-actions">
          {#if model.place === 'local' && model.fit}
            {#if model.state === 'offered' || model.state === 'failed'}<button
                class="secondary-button"
                onclick={() => service.downloadModel(model.id)}
                ><Download size={14} />Download</button
              >
            {:else if ['downloading', 'verifying', 'loading'].includes(model.state)}<button
                class="secondary-button"
                disabled={snapshot.attempt?.modelId === model.id ||
                  snapshot.reviewActivity?.modelId === model.id}
                onclick={() => service.cancelSetup(model.id)}><X size={14} />Cancel</button
              >
            {:else if model.state === 'downloaded'}<button
                class="secondary-button"
                onclick={() => service.loadModel(model.id)}><Play size={14} />Load</button
              ><button
                class="icon-button"
                aria-label={`Remove ${model.name}`}
                title="Remove sample model"
                onclick={() => service.unloadModel(model.id, true)}><Trash2 size={15} /></button
              >
            {:else}<button class="secondary-button" onclick={() => service.unloadModel(model.id)}
                ><Square size={12} />Unload</button
              ><button
                class="icon-button"
                aria-label={`Remove ${model.name}`}
                title="Remove sample model"
                onclick={() => service.unloadModel(model.id, true)}><Trash2 size={15} /></button
              >{/if}
          {:else if !model.fit}<span class="muted">Choose a smaller sample</span>{:else}<Check
              size={17}
              class="muted"
            />{/if}
        </div>
      </div>
    {/each}
  </div>
  <div class="management-note">
    <ShieldCheck size={18} />
    <p>
      All sizes, fit and setup states are fixtures. Download, verification and loading animate in
      memory. No model weights, network downloads or llama.cpp process are used.
    </p>
  </div>
</section>
