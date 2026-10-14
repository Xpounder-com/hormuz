<script lang="ts">
  import { tick } from 'svelte';
  import { Sparkles, ChevronDown, Check, Monitor, Cloud, Pin } from '@lucide/svelte';
  import type { Chat, RoutePlan, WorkspaceState } from '../lib/types.ts';
  let {
    snapshot,
    chat,
    plan,
    onchoose,
  }: { snapshot: WorkspaceState; chat: Chat; plan: RoutePlan; onchoose: (id: string) => void } =
    $props();
  let open = $state(false);
  let root: HTMLDivElement;
  let trigger: HTMLButtonElement;
  async function showMenu() {
    open = true;
    await tick();
    root.querySelector<HTMLButtonElement>('[role="menuitemradio"]')?.focus();
  }
  function close() {
    open = false;
    trigger?.focus();
  }
  function choose(id: string) {
    onchoose(id);
    close();
  }
  function keyboard(event: KeyboardEvent) {
    if (event.key === 'Escape') {
      event.stopPropagation();
      close();
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const options = Array.from(
        root.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]'),
      );
      const index = options.indexOf(document.activeElement as HTMLButtonElement);
      options[
        (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length
      ]?.focus();
    }
  }
</script>

<svelte:window
  onpointerdown={(event) => {
    if (open && root && !root.contains(event.target as Node)) open = false;
  }}
/>
<div class="model-picker" bind:this={root}>
  <button
    type="button"
    class:chosen={chat.intent === 'auto'}
    class="auto-button"
    title="Use Auto for this chat"
    aria-label="Use Auto for this chat"
    aria-pressed={chat.intent === 'auto'}
    onclick={() => onchoose('auto')}><Sparkles size={15} /></button
  >
  <button
    type="button"
    class="picker-trigger"
    bind:this={trigger}
    aria-label={`${plan.model?.name ?? 'Choose model'} ${plan.model?.place === 'cloud' ? 'Cloud' : 'Local'}`}
    aria-haspopup="menu"
    aria-expanded={open}
    title={`${plan.model?.name ?? 'No eligible model'} · ${plan.reason}`}
    onclick={() => (open ? close() : showMenu())}
    onkeydown={(event) => {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        showMenu();
      }
    }}
  >
    {#if plan.model?.place === 'cloud'}<Cloud size={14} />{:else}<Monitor size={14} />{/if}
    <span class="model-name">{plan.model?.name ?? 'Choose model'}</span>
    <span class:cloud={plan.model?.place === 'cloud'} class="location-dot"></span>
    <span class="location-label">{plan.model?.place === 'cloud' ? 'Cloud' : 'Local'}</span>
    {#if chat.intent !== 'auto'}<Pin size={12} class="pin-icon" />{/if}<ChevronDown size={13} />
  </button>
  {#if open}
    <div
      class="picker-menu glass"
      role="menu"
      aria-label="Model picker"
      onkeydown={keyboard}
      tabindex="-1"
    >
      <button
        type="button"
        role="menuitemradio"
        aria-checked={chat.intent === 'auto'}
        class="model-option"
        onclick={() => choose('auto')}
        ><Sparkles size={18} /><span
          ><strong>Auto</strong><small>Choose for this task · simulated</small></span
        >{#if chat.intent === 'auto'}<Check size={16} />{/if}</button
      >
      <div class="menu-label">LOCAL MODELS</div>
      {#each snapshot.models.filter((m) => m.place === 'local') as model}
        <button
          type="button"
          role="menuitemradio"
          aria-checked={chat.intent === model.id}
          class="model-option"
          onclick={() => choose(model.id)}
          ><Monitor size={17} /><span
            ><strong>{model.name}</strong><small
              >{model.fit
                ? model.state === 'downloaded'
                  ? 'Loads on Send'
                  : model.state
                : 'Fit unavailable'}</small
            ></span
          >{#if chat.intent === model.id}<Check size={16} />{/if}</button
        >
      {/each}
      <div class="menu-label">PAID MODELS · DEMO</div>
      {#each snapshot.models.filter((m) => m.place === 'cloud') as model}
        <button
          type="button"
          role="menuitemradio"
          aria-checked={chat.intent === model.id}
          class="model-option"
          onclick={() => choose(model.id)}
          ><Cloud size={17} /><span
            ><strong>{model.name}</strong><small
              >{plan.cloudAllowed
                ? 'Requires verified demo connection'
                : 'Blocked by Local-only'}</small
            ></span
          >{#if chat.intent === model.id}<Check size={16} />{/if}</button
        >
      {/each}
      <p class="menu-footnote">A selection pins this chat. Auto resumes routing.</p>
    </div>
  {/if}
</div>
