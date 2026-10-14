<script lang="ts">
  import { ArrowUp, Square, Paperclip, ArrowUpRight } from '@lucide/svelte';
  import ModelPicker from './ModelPicker.svelte';
  import type { Chat, RoutePlan, WorkspaceState } from '../lib/types.ts';
  let {
    snapshot,
    chat,
    plan,
    compact = false,
    ondraft,
    onpin,
    onsend,
    onstop,
    oncontext,
    onexpand,
  }: {
    snapshot: WorkspaceState;
    chat: Chat;
    plan: RoutePlan;
    compact?: boolean;
    ondraft: (text: string) => void;
    onpin: (id: string) => void;
    onsend: () => void;
    onstop: () => void;
    oncontext: () => void;
    onexpand?: () => void;
  } = $props();
  let input: HTMLTextAreaElement;
  let composing = false;
  const ownAttempt = $derived(snapshot.attempt?.chatId === chat.id);
  function submit() {
    if (chat.draft.trim() && !plan.blocked && !snapshot.attempt) onsend();
  }
  function key(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !composing) {
      event.preventDefault();
      submit();
    }
  }
  $effect(() => {
    if (compact) input?.focus();
  });
</script>

<form
  class:compact
  class="composer glass"
  onsubmit={(event) => {
    event.preventDefault();
    submit();
  }}
>
  <textarea
    bind:this={input}
    aria-label="Prompt"
    placeholder={compact ? 'Ask anything…' : 'Ask anything, or pick up where you left off…'}
    value={chat.draft}
    oninput={(event) => ondraft(event.currentTarget.value)}
    onkeydown={key}
    oncompositionstart={() => (composing = true)}
    oncompositionend={() => (composing = false)}
    rows={compact ? 1 : 2}
    maxlength="30000"></textarea>
  <div class="composer-tools">
    {#if !compact}<button
        type="button"
        class="icon-button attach"
        title="Select project context"
        aria-label="Select project context"
        onclick={oncontext}><Paperclip size={18} /></button
      >{/if}
    <ModelPicker {snapshot} {chat} {plan} onchoose={onpin} />
    <div class="composer-spacer"></div>
    {#if plan.preparation}<span class="load-hint">Loads on Send</span>{/if}
    {#if compact}<button
        type="button"
        class="icon-button"
        title="Open workspace"
        aria-label="Open workspace"
        onclick={onexpand}><ArrowUpRight size={17} /></button
      >{/if}
    {#if ownAttempt}<button
        type="button"
        class="send-button stop-button"
        aria-label="Stop response"
        title="Stop response"
        onclick={onstop}><Square size={13} fill="currentColor" /></button
      >
    {:else}<button
        type="submit"
        class="send-button"
        aria-label="Send prompt"
        title={snapshot.attempt
          ? 'Another response is active'
          : plan.blocked
            ? plan.reason
            : 'Send · Enter'}
        disabled={!chat.draft.trim() || plan.blocked || Boolean(snapshot.attempt)}
        ><ArrowUp size={20} /></button
      >{/if}
  </div>
</form>
{#if plan.blocked && chat.draft.trim()}<div class="route-message" role="status">
    <span class="warning-dot"></span>{plan.reason}<button onclick={oncontext}>Review setup</button>
  </div>
{:else if !compact}<div class="composer-caption">
    <span
      >{ownAttempt
        ? snapshot.attempt?.phase === 'preparing'
          ? 'Preparing local model · simulated'
          : 'Responding · simulated'
        : chat.intent === 'auto'
          ? 'Auto previews as you type'
          : 'Model pinned for this chat'}{plan.model?.place === 'cloud'
        ? ' · estimate $0.02, upper allowance $0.12 · fictional'
        : ''}</span
    ><span>Enter to send <span class="hint-separator">·</span> Shift + Enter for a new line</span>
  </div>{/if}
