<script lang="ts">
  import { X } from '@lucide/svelte';
  import type { Snippet } from 'svelte';
  let { title, onclose, children }: { title: string; onclose: () => void; children: Snippet } =
    $props();
  let dialog: HTMLDialogElement;
  $effect(() => {
    dialog.showModal();
    return () => dialog.close();
  });
</script>

<dialog
  bind:this={dialog}
  class="modal glass"
  aria-label={title}
  oncancel={(event) => {
    event.preventDefault();
    onclose();
  }}
  onclick={(event) => {
    if (event.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (
        event.clientX < r.left ||
        event.clientX > r.right ||
        event.clientY < r.top ||
        event.clientY > r.bottom
      )
        onclose();
    }
  }}
>
  <div class="modal-heading">
    <h2>{title}</h2>
    <button class="icon-button" aria-label="Close dialog" onclick={onclose}><X size={18} /></button>
  </div>
  {@render children()}
</dialog>
