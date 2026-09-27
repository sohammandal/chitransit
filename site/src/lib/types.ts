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
  gap_s: number | null; // time since the bus ahead passed this spot
  ahead: string | null; // vid of that bus
  at_terminal: boolean;
};

export type Feed = { generated_at: string; build?: string; vehicles: Vehicle[] }; // build: the poller's commit

export type Pattern = {
  rt: string;
  dir: string;
  c: [number, number][]; // [lon, lat] along the route
  s: [number, number, string, number][]; // stops: [lon, lat, name, feet along]
};

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
