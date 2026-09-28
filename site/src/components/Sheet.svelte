<script lang="ts">
  // The panel that holds the current level's content: a bottom sheet on phones, a side panel
  // under the top bar on wider screens. Each level puts its title (and tabs) in a
  // .sheet-head, which stays put while the rest scrolls. On phones the handle and the head
  // resize the sheet: drag them, or tap the handle to step peek → half → full.
  import type { Snippet } from "svelte";

  type Size = "peek" | "half" | "full";
  let { size, onSize, children }: {
    size: Size;
    onSize?: (size: Size) => void; // shown as the handle: a tap or drag settles on a size
    children: Snippet;
  } = $props();

  const TAP_PX = 6; // less movement than this is a tap
  const FLICK = 0.4; // px/ms: a release this fast moves one size its way

  let el: HTMLElement;
  let content: HTMLElement;
  let phone = $state(false);
  let fullPx = $state(0); // "full" = as tall as the content, never under the top bar
  let drag: { y: number; h: number; id: number; t: number; v: number; last: number } | null = null;
  let dragH = $state<number | null>(null); // the sheet's height while dragging
  let moved = false;
  let onGrab = false; // the drag started on the handle (a tap there steps the size)
  let swallow = false; // a drag that began on a button isn't a tap on it

  /** Heights of the three sizes now, matching the CSS below. */
  function heights(): Record<Size, number> {
    const vh = window.innerHeight;
    const bar = document.querySelector("header.bar")?.getBoundingClientRect().bottom ?? 0;
    const grab = (el.querySelector(".grab") as HTMLElement | null)?.offsetHeight ?? 0;
    const body = el.querySelector(".body") as HTMLElement;
    const pad = parseFloat(getComputedStyle(body).paddingTop) + parseFloat(getComputedStyle(body).paddingBottom);
    const half = vh * 0.56;
    return { peek: vh * 0.38, half, full: Math.max(half, Math.min(grab + content.offsetHeight + pad, vh - bar - 16)) };
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

  // "full" gets an explicit height (CSS can't animate to auto), kept in step with the content.
  $effect(() => {
    const narrow = window.matchMedia("(max-width: 640px)");
    const measure = () => { phone = narrow.matches; fullPx = heights().full; };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(content);
    narrow.addEventListener("change", measure);
    window.addEventListener("resize", measure);
    return () => { ro.disconnect(); narrow.removeEventListener("change", measure); window.removeEventListener("resize", measure); };
  });

  // Phones: the map's buttons ride just above the sheet (MapView reads --sheet), following
  // the finger while dragging (html[data-sheet-drag] turns their easing off).
  $effect(() => {
    const root = document.documentElement;
    root.style.setProperty("--sheet", dragH !== null ? `${dragH}px` : size === "peek" ? "38dvh" : size === "half" ? "56dvh" : `${fullPx}px`);
    if (dragH !== null) root.dataset.sheetDrag = "";
    else delete root.dataset.sheetDrag;
  });
  $effect(() => () => {
    stop();
    document.documentElement.style.setProperty("--sheet", "0px");
    delete document.documentElement.dataset.sheetDrag;
  });

  function down(e: PointerEvent) {
    const t = e.target as HTMLElement;
    if (!phone || !onSize || !e.isPrimary || e.button > 0 || !t.closest(".grab, .sheet-head")) return;
    drag = { y: e.clientY, h: el.offsetHeight, id: e.pointerId, t: e.timeStamp, v: 0, last: e.clientY };
    moved = false;
    onGrab = !!t.closest(".grab");
    // The rest of the drag is followed on the window: the finger or mouse soon leaves the head.
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
  }
  function stop() {
    drag = null;
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    window.removeEventListener("pointercancel", cancel);
  }
  function move(e: PointerEvent) {
    if (!drag || e.pointerId !== drag.id) return;
    const dy = drag.y - e.clientY; // up is positive
    if (Math.abs(dy) >= TAP_PX) moved = true;
    const dt = e.timeStamp - drag.t;
    if (dt > 0) drag.v = 0.7 * ((drag.last - e.clientY) / dt) + 0.3 * drag.v; // smoothed px/ms, up +
    drag.t = e.timeStamp;
    drag.last = e.clientY;
    if (!moved) return;
    // Follows the finger; past the smallest or largest size it gives way slowly (rubber band).
    const h = heights();
    const lo = h.peek, hi = h[sizes(h).at(-1)!];
    const want = drag.h + dy;
    dragH = want < lo ? lo - (lo - want) / 3 : want > hi ? hi + (want - hi) / 3 : want;
  }
  function up(e: PointerEvent) {
    if (!drag || e.pointerId !== drag.id) return;
    const { v } = drag;
    const at = dragH ?? drag.h;
    stop();
    if (!moved) {
      if (onGrab) step();
      return;
    }
    swallow = true;
    setTimeout(() => (swallow = false), 0);
    const h = heights();
    const order = sizes(h);
    // A flick moves one size its way; a slow release settles on the nearest size.
    let to = order.reduce((a, b) => (Math.abs(h[b] - at) < Math.abs(h[a] - at) ? b : a));
    if (Math.abs(v) >= FLICK) {
      const from = order.filter((s) => (v > 0 ? h[s] > at + 1 : h[s] < at - 1));
      to = from.length ? (v > 0 ? from[0] : from.at(-1)!) : to;
    }
    fullPx = h.full;
    dragH = null; // the CSS eases from here to the new size
    onSize?.(to);
  }
  function cancel() {
    stop();
    dragH = null;
  }
</script>

<!-- Dragging is a pointer shortcut; keyboards resize with the handle button. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<section
  class="sheet {size}"
  class:dragging={dragH !== null}
  bind:this={el}
  onpointerdown={down}
  onclickcapture={(e) => { if (swallow) { e.preventDefault(); e.stopPropagation(); swallow = false; } }}
  style:height={dragH !== null ? `${dragH}px` : phone && size === "full" && fullPx ? `${fullPx}px` : null}
  style:max-height={dragH !== null ? "none" : null}
>
  {#if onSize}
    <button
      class="grab"
      onclick={(e) => { if (e.detail === 0 || !phone) step(); }}
      aria-label={size === "full" ? "Show less" : "Show more"}
    ><i></i></button>
  {/if}
  <div class="body"><div class="content" bind:this={content}>{@render children()}</div></div>
</section>

<style>
  .sheet {
    background: var(--surface); border-radius: var(--radius); box-shadow: var(--shadow);
    display: flex; flex-direction: column; min-height: 0; overflow: hidden;
  }
  .body { overflow-y: auto; padding: 20px 16px 18px; }
  .content { display: grid; gap: 14px; align-content: start; }
  .grab { display: none; }
  /* The level's title (and tabs) stay put; the rest scrolls beneath them. */
  .content > :global(.sheet-head) {
    position: sticky; top: -20px; z-index: 2; display: grid; gap: 14px;
    margin: -20px -16px 0; padding: 20px 16px 0; background: var(--surface);
    box-shadow: 0 -12px 0 var(--surface); /* no sliver of scrolled content above it */
  }

  /* Phones: a bottom sheet with three heights, clear of the home indicator. */
  @media (max-width: 640px) {
    .sheet {
      position: fixed; left: 0; right: 0; bottom: 0; z-index: 3;
      border-radius: 20px 20px 0 0; transition: height 0.32s cubic-bezier(0.2, 0.8, 0.2, 1);
    }
    .peek { height: 38dvh; }
    .half { height: 56dvh; }
    /* Full: as tall as its content (the height is set inline), never under the top bar. */
    .full { min-height: 56dvh; max-height: calc(100dvh - var(--top-h, 180px) - 16px - var(--safe-top)); }
    .dragging { transition: none; }
    .body { padding-top: 10px; padding-bottom: calc(18px + var(--safe-bottom)); }
    .content > :global(.sheet-head) { top: -10px; margin-top: -10px; padding-top: 10px; touch-action: none; }
    /* A 30 px handle band across the sheet, and the head below it, both resize the sheet.
       touch-action: the drag moves the sheet, not the page. */
    .grab {
      position: relative; display: grid; place-items: center; width: 100%; height: 30px; flex: none;
      border: 0; background: none; padding: 6px 0 0; cursor: grab; touch-action: none;
    }
    .grab i { width: 44px; height: 5px; border-radius: 3px; background: color-mix(in srgb, var(--fog) 55%, var(--surface)); }
    .grab:hover i, .dragging .grab i { background: var(--fog); }
  }
  @media (prefers-reduced-motion: reduce) { .sheet { transition: none; } }
</style>
