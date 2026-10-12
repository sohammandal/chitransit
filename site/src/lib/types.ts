// Shapes of the files the site reads. The poller writes live/vehicles.json every 30 s and
// live/patterns/{pid}.json for every pattern (ghost_ledger.live); the nightly job writes
// everything under data/v1/ (transform/site_data.py).

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

export type Feed = {
  generated_at: string;
  build?: string; // the poller's commit
  no_data_since?: string | null; // CTA's feed down since then
  rerouted?: Record<string, string[]>; // route -> directions CTA has on a detour now ("": all)
  alerts?: Record<string, Alert[]>; // route -> CTA's reroute alerts in force, newest first
  vehicles: Vehicle[];
};

/** One of CTA's Customer Alerts: its headline and CTA's page about it. */
export type Alert = { h: string; u: string | null };

/** A stop riders board at (same-named stops at a corner merged): [lon, lat, name, pids]. */
export type Stop = [number, number, string, number[]];

export type Pattern = {
  rt: string;
  dir: string;
  c: [number, number][]; // [lon, lat] along the route
  s: [number, number, string, number, number?, string?][]; // stops: [lon, lat, name, feet along, routes at that crossing, CTA stop id]
  d?: [number, number][]; // a reroute: the regular path it replaces (CTA's own map dashes it)
  x?: [number, number, string, string][]; // a reroute: the regular stops it skips [lon, lat, name, CTA stop id]
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

/** Where the map's camera is: kept with each page of history, so Back returns to it. */
export type Camera = { center: [number, number]; zoom: number };

// The 'L': live/trains.json and live/stations.json (the poller's train sweep, every 20 s;
// ghost_ledger.live_trains), and l/lines.json (the lines as drawn, from CTA's GTFS:
// scripts/l_lines.py). CTA's own data, shown as CTA sent it. rt is the line's Train Tracker
// code (red, blue, brn, g, org, p, pink, y) everywhere.

/** One train, as Train Tracker places it (track circuits, not GPS: positions jump). */
export type Train = {
  rn: string; // run number ("812")
  rt: string;
  dir: string; // CTA's operational direction: "1" or "5"
  dest: string | null; // "Howard"
  next: string; // the next station's map id ("40380")
  next_name: string | null;
  arr: number | null; // CTA's time for it at the next station (epoch seconds)
  app: boolean; // CTA says approaching
  dly: boolean; // CTA says delayed (hasn't moved for an abnormally long time)
  lat: number | null; // null: CTA can't place it
  lon: number | null;
  hdg: number | null;
  t: number | null; // when CTA last updated it (epoch seconds)
  // Our claims (live_trains.py), as the buses' are, measured at its next platform:
  gap_s?: number | null; // since the last train passed it (null: can't tell)
  last_pass?: number | null; // when (epoch seconds)
  last_by?: string | null; // that train ("red:812")
  ahead_s?: number | null; // seconds between it and that train, by CTA's times (the strip's minutes)
  stop?: string; // its next platform
  long_wait?: boolean; // over twice the scheduled gap there
  together_with?: string | null; // the train just ahead at its last platform, when together
  together_s?: number | null;
};

export type TrainFeed = {
  generated_at: string;
  build?: string;
  data_at: number | null; // CTA's newest update in it (epoch seconds)
  trains: Train[];
  status: Record<string, string>; // line -> CTA's words ("Normal Service")
};

/** One of CTA's predictions at a station. */
export type Eta = {
  rn: string;
  rt: string;
  dest: string | null;
  stop: string; // the platform's stop id
  stop_desc: string | null; // "Service toward Howard"
  arr: number | null; // epoch seconds
  t: number | null;
  sch: boolean; // from the timetable: no train behind it yet
  app: boolean;
  dly: boolean;
  flt: boolean; // CTA flags a fault (on a timetable entry: its departure didn't happen)
};

export type StationEtas = { generated_at: string; stations: Record<string, { at: number; eta: Eta[] }> };

export type Line = {
  name: string; // "Red Line"
  color: string;
  text: string; // text colour on the line's colour
  shapes: [number, number][][];
  w?: number[]; // each shape piece's width in stripes: n lines share its track (l_lines.py)
  stations: string[][]; // each shape's stations in order (map ids)
};

export type Lines = {
  feed: string;
  lines: Record<string, Line>;
  stations: Record<string, { name: string; lon: number; lat: number; lines: string[] }>;
};

/** What the map shows: both, or just one. */
export type Mode = "all" | "bus" | "train";
