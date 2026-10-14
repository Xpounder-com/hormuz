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
  import Modal from './Modal.svelte';
  let { snapshot, service }: { snapshot: WorkspaceState; service: MockWorkspace } = $props();
  let filter = $state('all');
  let details = $state('');
  let removing = $state('');
  const selected = $derived(snapshot.models.find((m) => m.id === (removing || details)));
  const models = $derived(
    snapshot.models.filter(
      (m) =>
        filter === 'all' ||
        m.place === filter ||
        (filter === 'installed' &&
          m.place === 'local' &&
          ['downloaded', 'loading', 'ready'].includes(m.state)),
    ),
  );
</script>

<section class="management-view" aria-label="Model library">
  <div class="page-heading">
    <span class="page-icon"><Monitor size={23} /></span>
    <h1>Models</h1>
    <p>One local model loaded at a time. Auto can load a downloaded model on Send.</p>
  </div>
  <div class="segmented" aria-label="Filter models">
    {#each [['all', 'Discover'], ['installed', 'My models'], ['local', 'Local'], ['cloud', 'Cloud']] as [value, label]}<button
        class:selected={filter === value}
        aria-pressed={filter === value}
        onclick={() => (filter = value)}>{label}</button
      >{/each}
  </div>
  {#if snapshot.catalog === 'unavailable'}<div class="state-panel" role="status">
      <h2>Catalog unavailable</h2>
      <p>Already installed models stay available. Nothing is fetched while typing.</p>
      <button class="secondary-button" onclick={() => service.setFixture('catalog', 'available')}
        >Retry sample catalog</button
      >
    </div>{/if}
  <div class="model-library glass">
    {#each models as model}
      <div class="library-row">
        <span class:cloud={model.place === 'cloud'} class="model-symbol"
          >{#if model.place === 'local'}<Monitor size={21} />{:else}<Cloud size={21} />{/if}</span
        >
        <div class="model-info">
          <button
            class="model-detail-link"
            onclick={() => (details = model.id)}
            aria-label={`Details for ${model.name}`}><strong>{model.name}</strong></button
          ><span>{model.detail}</span><small
            >{model.size} · {model.place === 'local'
              ? 'llama.cpp simulation'
              : 'Demo provider'}</small
          >{#if ['downloading', 'verifying', 'loading'].includes(model.state)}<progress
              value={model.progress}
              max="100"
              aria-label={`${model.name} setup progress`}
            ></progress>{/if}{#if model.problem}<small role="status" class="model-problem"
              >{model.problem}</small
            >{/if}
        </div>
        <span class:ready={model.state === 'ready' && model.fit} class="model-state"
          >{!model.fit
            ? 'Fit unavailable'
            : model.place === 'cloud'
              ? snapshot.connection.status === 'verified'
                ? !snapshot.connection.provider ||
                  snapshot.connection.provider === 'demo' ||
                  snapshot.connection.provider === model.id
                  ? 'Demo verified'
                  : 'Needs its provider'
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
                onclick={() => (removing = model.id)}><Trash2 size={15} /></button
              >
            {:else}<button class="secondary-button" onclick={() => service.unloadModel(model.id)}
                ><Square size={12} />Unload</button
              ><button
                class="icon-button"
                aria-label={`Remove ${model.name}`}
                title="Remove sample model"
                onclick={() => (removing = model.id)}><Trash2 size={15} /></button
              >{/if}
          {:else if !model.fit}<span class="muted">Choose a smaller sample</span>{:else}<Check
              size={17}
              class="muted"
            />{/if}
        </div>
      </div>
    {/each}
  </div>
  {#if !models.length}<div class="state-panel">
      <h2>No models here yet.</h2>
      <p>Discover a supported local sample and explicitly start setup. No sign-in is required.</p>
    </div>{/if}
  <div class="management-note">
    <ShieldCheck size={18} />
    <p>
      All sizes, fit and setup states are fixtures. Download, verification and loading animate in
      memory. No model weights, network downloads or llama.cpp process are used.
    </p>
  </div>
</section>
{#if selected && (details || removing)}<Modal
    title={removing ? 'Remove app-owned model?' : selected.name}
    onclose={() => {
      details = '';
      removing = '';
    }}
    >{#if removing}<p class="modal-description">
        Remove only this model’s app-owned sample package from inventory. Existing projects,
        conversation history, usage and external originals stay intact. Affected pins/defaults
        remain selected and blocked; nothing becomes a cloud request.
      </p>
      <p class="panel-description">
        Affected chat pins: {snapshot.chats.filter(
          (c) => c.intent === selected.id || c.requestIntent === selected.id,
        ).length}. Project defaults: {snapshot.projects.filter((p) => p.modelIntent === selected.id)
          .length}.
      </p>
      <div class="modal-actions">
        <button class="secondary-button" onclick={() => (removing = '')}>Cancel</button><button
          class="primary-button danger"
          disabled={snapshot.attempt?.modelId === selected.id ||
            snapshot.reviewActivity?.modelId === selected.id ||
            selected.state === 'loading'}
          onclick={() => {
            service.unloadModel(selected.id, true);
            removing = '';
          }}>Remove sample package</button
        >
      </div>{:else}<div class="save-preview">
        <strong>{selected.detail}</strong><span
          >{selected.place === 'local'
            ? 'Local text model fixture · llama.cpp / GGUF path'
            : 'Paid API fixture · optional binding'}</span
        >
        <p>
          Source/version/license: illustrative inventory only; exact artifact, upstream license and
          engine compatibility require backend qualification. No marketplace loader runs.
        </p>
        <p>
          Capabilities: text conversation {selected.specialty === 'writing'
            ? 'and writing'
            : selected.specialty === 'research'
              ? 'and research fixtures'
              : 'and general tasks'}. Context: {selected.inputLimit || 'unknown'} sample input tokens.
        </p>
        <p>
          Hardware fit: {selected.fit
            ? 'fictional qualified fit'
            : 'unknown / unsupported; setup blocked'}. Size: {selected.size}. File size is not a
          performance or fit guarantee.
        </p>
        <p>
          Storage: app-owned model store, simulated. Download and loading are separate; a downloaded
          package is not Ready.
        </p>
      </div>
      <div class="modal-actions">
        <button class="secondary-button" onclick={() => (details = '')}>Close</button>
      </div>{/if}</Modal
  >{/if}
