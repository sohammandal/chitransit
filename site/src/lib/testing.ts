// For unit tests: a bus as the live feed has it (every field), quiet unless `o` says otherwise:
// no wait known, no claims.
import type { Vehicle } from "./types";

export const bus = (o: Partial<Vehicle> = {}): Vehicle => ({
  vid: "A", rt: "9", pid: 1, hdg: 0, lat: 41.8, lon: -87.66, dly: false, des: null, dir: "Northbound", t: 0,
  pdist: 0, at_terminal: false, near: null, stop_gap_s: null, last_by: null, next_stop: null, next_stpid: null,
  sched_s: null, sched_due: null, long_wait: false, together_with: null, together_s: null,
  behind_vid: null, behind_stops: null, trip: null, ...o,
});
