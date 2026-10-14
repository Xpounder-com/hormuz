<script lang="ts">
  import { Copy, Check, Monitor, Cloud, Sparkles } from '@lucide/svelte';
  import type { Chat } from '../lib/types.ts';
  let { chat, ondraft }: { chat: Chat; ondraft: (text: string) => void } = $props();
  let copied = $state('');
  let copyError = $state('');
  let end: HTMLDivElement;
  let follow = $state(true);
  $effect(() => {
    chat.messages.at(-1)?.text;
    chat.id;
    if (follow) end?.scrollIntoView({ block: 'end' });
  });
  async function copy(id: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
      copied = id;
      copyError = '';
    } catch {
      copyError = 'Copy is unavailable in this browser. Select the text to copy it.';
    }
  }
</script>

<div
  class="conversation-scroll"
  onscroll={(event) => {
    const el = event.currentTarget;
    follow = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
  }}
>
  <div class="conversation">
    {#if chat.messages.length === 0}
      <div class="empty-conversation">
        <span class="welcome-mark"><Sparkles size={29} /></span>
        <h1>A little space to think.</h1>
        <p>
          {chat.projectId
            ? 'Start a conversation. Your project context is here when you need it.'
            : 'A Temporary chat stays in memory until you save or discard it.'}
        </p>
        <div class="starter-prompts">
          <button onclick={() => ondraft('Compare local and cloud models for my research')}
            >Explore an idea</button
          ><button onclick={() => ondraft('Write a clear first draft for my project')}
            >Start a draft</button
          >
        </div>
      </div>
    {:else}
      <div class="conversation-date">Today · sample workspace</div>
      {#each chat.messages as message (message.id)}
        <article
          class:user-message={message.role === 'user'}
          class:assistant-message={message.role === 'assistant'}
          aria-label={message.role === 'user' ? 'Your message' : 'Simulated assistant response'}
        >
          {#if message.role === 'assistant'}<div class="answer-label">
              <span class="mini-brand">H</span><strong>Hormuz</strong><span>Simulated response</span
              >
            </div>{/if}
          {#if message.text}<div class="message-body">{message.text}</div>
          {:else if message.receipt?.status === 'canceled' || message.receipt?.status === 'blocked'}<p
              class="terminal-empty"
            >
              {message.receipt.status === 'canceled'
                ? 'Stopped before output.'
                : 'Nothing dispatched. Review the changed context and send again.'}
            </p>
          {:else}<div class="thinking">
              <span></span><span></span><span></span><span class="sr-only"
                >{message.receipt?.status === 'preparing' ? 'Preparing model' : 'Responding'}</span
              >
            </div>{/if}
          {#if message.receipt}
            <div class="response-footer">
              <span
                >{#if message.receipt.place === 'local'}<Monitor size={12} />{:else}<Cloud
                    size={12}
                  />{/if}{message.receipt.model}<span class="receipt-status"
                  >{message.receipt.status === 'completed'
                    ? message.receipt.place === 'cloud'
                      ? '$0.02 · fictional actual'
                      : 'Local · no external inference charge'
                    : message.receipt.status === 'canceled'
                      ? message.receipt.held
                        ? 'Partial · fictional cost unknown'
                        : 'Stopped · partial'
                      : message.receipt.status}</span
                ></span
              ><button
                class="icon-button"
                aria-label="Copy response"
                title="Copy response"
                disabled={!message.text}
                onclick={() => copy(message.id, message.text)}
                >{#if copied === message.id}<Check size={14} />{:else}<Copy
                    size={14}
                  />{/if}</button
              >
            </div>
            <details class="receipt-details">
              <summary>Request context</summary>
              <p>
                Origin: {message.receipt.origin}. {message.receipt.context.length
                  ? `Included: ${message.receipt.context.join(', ')}.`
                  : 'No project files included.'}
              </p>
              <p>
                Frozen instructions: {message.receipt.instructions || 'Sample opening response.'}
              </p>
              {#if message.receipt.held}<p>
                  Fictional hold: ${message.receipt.held.toFixed(2)}. Discarding content does not
                  refund it.
                </p>{/if}
            </details>
          {/if}
        </article>
      {/each}
    {/if}
    {#if copyError}<p role="status" class="muted">{copyError}</p>{/if}
    <div bind:this={end}></div>
  </div>
</div>
