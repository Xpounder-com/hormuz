<script lang="ts">
  import { Copy, Check } from '@lucide/svelte';
  import { textBlocks } from '../lib/markdown.ts';
  let { text }: { text: string } = $props();
  let copied = $state(-1);
  let error = $state('');
  const blocks = $derived(textBlocks(text));
  async function copy(index: number, value: string) {
    try {
      await navigator.clipboard.writeText(value);
      copied = index;
      error = '';
    } catch {
      error = 'Copy unavailable. Select the code to copy it.';
    }
  }
</script>

<div class="message-body">
  {#each blocks as block, index}{#if block.kind === 'code'}<div class="code-block">
        <div class="code-heading">
          <span>{block.language || 'Code'} · text only</span><button
            class="icon-button"
            aria-label="Copy code"
            onclick={() => copy(index, block.text)}
            >{#if copied === index}<Check size={14} />{:else}<Copy size={14} />{/if}</button
          >
        </div>
        <pre><code>{block.text}</code></pre>
      </div>{:else if block.kind === 'heading'}<h3>
        {block.text}
      </h3>{:else if block.kind === 'list'}<p class="message-list-item">
        <span aria-hidden="true">•</span>{block.text}
      </p>{:else}<p>
        {#each block.text.split(/(\*\*[^*]+\*\*)/) as segment}{#if segment.startsWith('**') && segment.endsWith('**')}<strong
              >{segment.slice(2, -2)}</strong
            >{:else}{segment}{/if}{/each}
      </p>{/if}{/each}
  {#if error}<p class="muted" role="status">{error}</p>{/if}
</div>
