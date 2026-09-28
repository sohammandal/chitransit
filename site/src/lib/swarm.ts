/** Vertical offsets for dots on a horizontal line, so no two overlap (a 1-D beeswarm).
 *
 *  Each dot keeps its true x and leaves the line only when it would touch a dot already
 *  placed: buses at the same spot stack, a string of close buses zigzags along its real
 *  stretch, and buses far apart stay on the line. (Chaining close pairs into one stack drew
 *  8 buses spread over 1.5 miles of #66 as a single tower, Sep 28.)
 *
 *  xs: dot centres in order along the line; gap: the minimum distance between centres.
 *  Each run of dots that touch in x is then centred on the line, so a pair sits ±gap/2. */
export function swarm(xs: number[], gap: number): number[] {
  const dy: number[] = [];
  const order = xs.map((x, i) => [x, i] as const).sort((a, b) => a[0] - b[0]);
  const placed: { x: number; y: number }[] = [];
  for (const [x, i] of order) {
    const near = placed.filter((p) => Math.abs(p.x - x) < gap);
    for (let k = 0; ; k++) {
      const y = k === 0 ? 0 : (k % 2 ? -1 : 1) * Math.ceil(k / 2) * gap; // 0, -g, +g, -2g, ...
      if (near.every((p) => Math.hypot(p.x - x, p.y - y) >= gap - 1e-9)) {
        placed.push({ x, y });
        dy[i] = y;
        break;
      }
    }
  }
  // Centre each run of dots that touch in x (a run ends where the next dot is gap or more on).
  let start = 0;
  for (let j = 1; j <= order.length; j++) {
    if (j < order.length && order[j][0] - order[j - 1][0] < gap) continue;
    const run = order.slice(start, j).map(([, i]) => i);
    const ys = run.map((i) => dy[i]);
    const mid = (Math.max(...ys) + Math.min(...ys)) / 2;
    for (const i of run) dy[i] -= mid;
    start = j;
  }
  return dy;
}
