// Shapes of the files the site reads. The poller writes live/vehicles.json every 30 s;
// the nightly job writes everything under data/v1/ (transform/site_data.py).

export type Vehicle = {
  vid: string;
  rt: string;
  pid: number | null;
  hdg: number | null;
  lat: number | null;
  lon: number | null;
  dly: boolean;
  des: string | null;
  dir: string | null; // "Northbound"
  t: number; // when the bus reported this position (epoch seconds)
  pdist: number | null; // feet along its pattern
  at_terminal: boolean;
  near: string | null; // the stop nearest the bus ("Clark & Belmont")
  // Measured at the bus's next stop, counting every bus of the route and direction (short
  // turns too), and the schedule there.
  stop_gap_s: number | null; // time since the last bus passed the next stop
  last_by: string | null; // the bus that did
  next_stop: string | null; // "Clark & Belmont"
  next_stpid: string | null;
  sched_s: number | null; // scheduled gap at that stop now (null: nothing scheduled this hour)
  sched_due: number | null; // scheduled departures there since the last bus passed
  // The claims, decided by the poller (ghost_ledger.live docstring).
  long_wait: boolean;
  together_with: string | null; // the bus just ahead at its last stop, when together
  together_s: number | null;
  behind_vid: string | null; // the nearest bus behind that stops at its next stop
  behind_stops: number | null; // how many of this bus's stops back it is (0: the same next stop)
  trip: "starting" | "finishing" | null; // why a bus has no next stop
};

export type Feed = { generated_at: string; build?: string; no_data_since?: string | null; vehicles: Vehicle[] }; // build: the poller's commit; no_data_since: CTA's feed down since then

/** A stop riders board at (same-named stops at a corner merged): [lon, lat, name, pids]. */
export type Stop = [number, number, string, number[]];

export type Pattern = {
  rt: string;
  dir: string;
  c: [number, number][]; // [lon, lat] along the route
  s: [number, number, string, number, number?, string?][]; // stops: [lon, lat, name, feet along, routes at that crossing, CTA stop id]
};

/** live/stops.json: every stop's own wait, "rt|Northbound" -> stop id -> [seconds since the
 *  last bus passed it (null: can't tell), 1 if that's a long wait]. */
export type StopWaits = { generated_at: string; stops: Record<string, Record<string, [number | null, number]>> };

/** date -> "rt|North" -> Chicago hour -> scheduled minutes between buses */
export type Headways = Record<string, Record<string, Record<string, number>>>;

export type RouteDay = {
  scheduled: number;
  ran: number;
  announced: number;
  silent_proven: number;
  silent_possible: number;
  not_judged: number;
};

export type Summary = { date: string; routes: Record<string, RouteDay> };

/** The report card: every watched day so far. Rows align with `days`:
 *  [scheduled, ran (incl. likely ran), canceled with notice, proven no-show, couldn't tell] */
export type ReportRow = [number, number, number, number, number];
export type Report = { days: string[]; city: ReportRow[]; routes: Record<string, ReportRow[]> };
