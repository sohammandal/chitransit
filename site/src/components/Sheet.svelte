<script lang="ts">
  // The panel that holds the current level's content: a bottom sheet on phones (tap the
  // handle to grow or shrink it), a side panel under the top bar on wider screens.
  import type { Snippet } from "svelte";

  let { size, onToggle, children }: {
    size: "peek" | "half" | "full";
    onToggle?: () => void; // shown as the handle when the content can grow
    children: Snippet;
  } = $props();
</script>

<section class="sheet {size}">
  {#if onToggle}
    <button class="grab" onclick={onToggle} aria-label={size === "full" ? "Show less" : "Show more"}><i></i></button>
  {/if}
  <div class="body">{@render children()}</div>
</section>

<style>
  .sheet {
    background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow);
    display: flex; flex-direction: column; min-height: 0; overflow: hidden;
  }
  .body { overflow-y: auto; padding: 14px 16px 18px; display: grid; gap: 14px; align-content: start; }
  .grab { display: none; }

  /* Phones: a bottom sheet with three heights, clear of the home indicator. */
  @media (max-width: 640px) {
    .sheet {
      position: fixed; left: 0; right: 0; bottom: 0; z-index: 3;
      border-radius: 20px 20px 0 0; transition: height 0.25s ease;
    }
    .peek { height: 38dvh; }
    .half { height: 56dvh; }
    /* Full: as tall as its content, never under the top bar (App sets --top-h). */
    .full { height: auto; min-height: 56dvh; max-height: calc(100dvh - var(--top-h, 180px) - 16px - var(--safe-top)); }
    .body { padding-bottom: calc(18px + var(--safe-bottom)); }
    .grab { display: grid; place-items: center; width: 100%; border: 0; background: none; padding: 8px 0 2px; cursor: pointer; }
    .grab i { width: 38px; height: 5px; border-radius: 3px; background: var(--rule); }
  }
  @media (prefers-reduced-motion: reduce) { .sheet { transition: none; } }
</style>
