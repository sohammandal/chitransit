<script lang="ts">
  // The panel that holds the current level's content: a bottom sheet on phones (drag the
  // handle to resize it, or tap it to step peek → half → full), a side panel under the top
  // bar on wider screens.
  import type { Snippet } from "svelte";

  type Size = "peek" | "half" | "full";
  let { size, onSize, children }: {
    size: Size;
    onSize?: (size: Size) => void; // shown as the handle: a tap or drag settles on a size
    children: Snippet;
  } = $props();

  const TAP_PX = 6; // less movement than this is a tap
  const FLICK_PX = 40; // a drag this far always moves at least one size

  let el: HTMLElement;
  let drag = $state<{ y: number; h: number; id: number } | null>(null);
  let dragH = $state<number | null>(null); // the sheet's height while dragging
  let moved = false;

  /** Heights of the three sizes now, matching the CSS below. */
  function heights(): Record<Size, number> {
    const vh = window.innerHeight;
    const bar = document.querySelector("header.bar")?.getBoundingClientRect().bottom ?? 0;
    const max = vh - bar - 16; // never under the top bar
    const content = (el.querySelector(".grab") as HTMLElement).offsetHeight + (el.querySelector(".body") as HTMLElement).scrollHeight;
    const half = vh * 0.56;
    return { peek: vh * 0.38, half, full: Math.max(half, Math.min(content, max)) };
  }

  /** The sizes worth stopping at: "full" only when the content is taller than "half". */
  function sizes(h: Record<Size, number>): Size[] {
    return h.full - h.half > 8 ? ["peek", "half", "full"] : ["peek", "half"];
  }
  /** A tap (or Enter) steps to the next size, round to the smallest. */
  function step() {
    const order = sizes(heights());
    onSize?.(order[(order.indexOf(size) + 1) % order.length] ?? "half");
  }

  function down(e: PointerEvent) {
    drag = { y: e.clientY, h: el.offsetHeight, id: e.pointerId };
    moved = false;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }
  function move(e: PointerEvent) {
    if (!drag || e.pointerId !== drag.id) return;
    const dy = drag.y - e.clientY; // up is positive
    if (Math.abs(dy) >= TAP_PX) moved = true;
    if (!moved) return;
    const h = heights();
    dragH = Math.min(Math.max(drag.h + dy, h.peek * 0.8), Math.max(h.full, h.half));
  }
  function up(e: PointerEvent) {
    if (!drag || e.pointerId !== drag.id) return;
    const dy = drag.y - e.clientY;
    const start = drag.h;
    drag = null;
    dragH = null;
    if (!moved) return step();
    const h = heights();
    const order = sizes(h);
    // The nearest size to where it was let go; a flick moves at least one size its way.
    let to = order.reduce((a, b) => (Math.abs(h[b] - (start + dy)) < Math.abs(h[a] - (start + dy)) ? b : a));
    const i = order.indexOf(size);
    if (to === size && Math.abs(dy) >= FLICK_PX) to = order[Math.min(Math.max(i + Math.sign(dy), 0), order.length - 1)];
    onSize?.(to);
  }
</script>

<section class="sheet {size}" class:dragging={dragH !== null} bind:this={el} style:height={dragH !== null ? `${dragH}px` : null} style:max-height={dragH !== null ? "none" : null}>
  {#if onSize}
    <button
      class="grab"
      onpointerdown={down}
      onpointermove={move}
      onpointerup={up}
      onpointercancel={() => { drag = null; dragH = null; }}
      onclick={(e) => { if (e.detail === 0) step(); }}
      aria-label={size === "full" ? "Show less" : "Show more"}
    ><i></i></button>
  {/if}
  <div class="body">{@render children()}</div>
</section>

<style>
  .sheet {
    background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow);
    display: flex; flex-direction: column; min-height: 0; overflow: hidden;
  }
  .body { overflow-y: auto; padding: 20px 16px 18px; display: grid; gap: 14px; align-content: start; }
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
    .dragging { transition: none; }
    /* touch-action: the drag moves the sheet, not the page. The ::after widens the hit area
       into the body's empty top padding without moving anything. */
    .grab { position: relative; display: grid; place-items: center; width: 100%; border: 0; background: none; padding: 8px 0 2px; cursor: grab; touch-action: none; }
    .grab::after { content: ""; position: absolute; inset: 0 0 -14px; }
    .grab i { width: 38px; height: 5px; border-radius: 3px; background: var(--rule); }
  }
  @media (prefers-reduced-motion: reduce) { .sheet { transition: none; } }
</style>
