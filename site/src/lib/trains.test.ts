import { describe, expect, it } from "vitest";
import { etaText, gaps, lineDirections, lineOf, lineUrl, matchLines, nextText, platforms, stationsToGo, strip, together } from "./trains";
import type { Eta, Lines, Train } from "./types";
import drawn from "../../public/l/lines.json";

const lines: Lines = {
  feed: "2026-09-14",
  stations: {
    "40900": { name: "Howard", lon: 0, lat: 0, lines: ["red"] },
    "40380": { name: "Clark/Lake", lon: 0, lat: 0, lines: ["brn"] },
    "41450": { name: "Chicago", lon: 0, lat: 0, lines: ["red"] },
    "40450": { name: "95th/Dan Ryan", lon: 0, lat: 0, lines: ["red"] },
    "41290": { name: "Kimball", lon: 0, lat: 0, lines: ["brn"] },
    "41320": { name: "Belmont", lon: 0, lat: 0, lines: ["brn", "red"] },
  },
  lines: {
    red: { name: "Red Line", color: "#c60c30", text: "#fff", shapes: [], stations: [["40900", "41450", "40450"], ["40450", "41450", "40900"]] },
    // a Loop line: one path, out and back (Belmont twice)
    brn: { name: "Brown Line", color: "#62361b", text: "#fff", shapes: [], stations: [["41290", "41320", "40380", "41320", "41290"]] },
  },
};

const train = (rn: string, rt: string, dir: string, dest: string, next: string): Train => ({
  rn, rt, dir, dest, next, next_name: null, arr: null, app: false, dly: false, lat: null, lon: null, hdg: null, t: null,
});

describe("trains", () => {
  it("shows a platform board's time: Due within a minute or when approaching", () => {
    expect(etaText(1_000 + 4 * 60, false, 1_000)).toBe("4 min");
    expect(etaText(1_000 + 70, false, 1_000)).toBe("Due");
    expect(etaText(1_000 + 600, true, 1_000)).toBe("Due");
    expect(etaText(null, false, 1_000)).toBe("");
    // CTA flags a train that hasn't moved: its boards say Delayed, not its stale time
    expect(etaText(1_000 + 30, true, 1_000, true)).toBe("Delayed");
  });

  it("says a train is arriving at its next station, else when it gets there", () => {
    expect(nextText(1_000 + 30, false, 1_000)).toBe("Arriving");
    expect(nextText(1_000 + 300, true, 1_000)).toBe("Arriving");
    expect(nextText(1_000 + 180, false, 1_000)).toBe("in 3 min");
    expect(nextText(1_000 + 30, false, 1_000, true)).toBe("Delayed");
  });

  it("gives each line an address in riders' words, and reads CTA's codes too", () => {
    expect(lineUrl("brn")).toBe("/line/brown/");
    expect(lineUrl("red")).toBe("/line/red/");
    expect(lineOf("brown")).toBe("brn");
    expect(lineOf("Brn")).toBe("brn");
    expect(lineOf("teal")).toBeNull();
  });

  it("finds lines by name, not by one letter", () => {
    expect(matchLines("bl")).toEqual(["blue"]);
    expect(matchLines("Brown Line")).toEqual(["brn"]);
    expect(matchLines("p")).toEqual([]);
    expect(matchLines("pi")).toEqual(["pink"]);
  });

  it("counts stations to a destination, the Loop as the first Loop station ahead", () => {
    expect(stationsToGo(train("1", "red", "5", "95th/Dan Ryan", "41450"), lines)).toBe(1);
    expect(stationsToGo(train("2", "red", "1", "Howard", "41450"), lines)).toBe(1); // the other path
    expect(stationsToGo(train("3", "brn", "5", "Loop", "41320"), lines)).toBe(1);
    expect(stationsToGo(train("4", "brn", "1", "Kimball", "41320"), lines)).toBe(1); // the nearer Belmont
    expect(stationsToGo(train("5", "red", "5", "Somewhere", "41450"), lines)).toBeNull();
  });

  it("groups a line's trains by destination, each nearest its destination first", () => {
    const dirs = lineDirections([
      train("801", "red", "5", "95th/Dan Ryan", "40900"),
      train("802", "red", "5", "95th/Dan Ryan", "41450"),
      train("901", "red", "1", "Howard", "41450"),
      train("501", "brn", "1", "Kimball", "41320"),
    ], "red", lines);
    expect(dirs.map((d) => [d.toward, d.trains.map((t) => t.rn)])).toEqual([["Howard", ["901"]], ["95th/Dan Ryan", ["802", "801"]]]);
    // Green's branches share CTA's direction code: one group per destination
    const g = lineDirections([
      train("019", "g", "5", "Ashland/63rd", "x"), train("611", "g", "5", "Cottage Grove", "x"),
      train("020", "g", "5", "Ashland/63rd", "x"), train("011", "g", "1", "Harlem/Lake", "x"),
    ], "g", lines);
    expect(g.map((d) => [d.toward, d.trains.length])).toEqual([["Harlem/Lake", 1], ["Ashland/63rd", 2], ["Cottage Grove", 1]]);
  });

  it("draws a direction as a strip: stations by distance, trains between their last and next station", () => {
    const st = (name: string, lat: number, lines_: string[]) => ({ name, lon: -87.6, lat, lines: lines_ });
    const l: Lines = {
      feed: "x",
      stations: {
        a: st("A", 42.0, ["red"]), b: st("B", 41.99, ["red"]), c: st("C", 41.96, ["red"]),
        k: st("Kimball", 41.97, ["brn"]), m: st("Mid", 41.93, ["brn"]), "40380": st("Clark/Lake", 41.886, ["brn"]),
        "40260": st("State/Lake", 41.8858, ["brn"]),
      },
      lines: {
        red: { name: "Red Line", color: "#c60c30", text: "#fff", shapes: [], stations: [["a", "b", "c"], ["c", "b", "a"]] },
        brn: { name: "Brown Line", color: "#62361b", text: "#fff", shapes: [], stations: [["k", "m", "40380", "40260", "m", "k"]] },
      },
    };
    const at = (rn: string, rt: string, dest: string, next: string, lat: number) => ({ ...train(rn, rt, "5", dest, next), lat, lon: -87.6 });
    const s = strip([at("1", "red", "C", "c", 41.975), at("2", "red", "C", "b", 41.995), at("3", "red", "A", "b", 41.97)], "red", "C", l)!;
    expect(s.seq).toEqual(["a", "b", "c"]);
    expect(s.at.map((x) => +x.toFixed(2))).toEqual([0, 0.25, 1]); // 0.01 then 0.03 degrees
    expect(s.trains.map((p) => p.t.rn)).toEqual(["2", "1"]); // in order along it
    expect(s.trains[0].x).toBeCloseTo(0.125, 2); // halfway from A to B
    expect(s.trains[1].x).toBeCloseTo(0.625, 2); // halfway from B to C
    // a Loop line's path cut at the Loop: toward the Loop up to the end of the circuit
    const loop = strip([at("4", "brn", "Loop", "m", 41.95)], "brn", "Loop", l)!;
    expect(loop.seq).toEqual(["k", "m", "40380", "40260"]);
    expect(strip([at("5", "brn", "Kimball", "k", 41.95)], "brn", "Kimball", l)!.seq).toEqual(["40380", "40260", "m", "k"]);
    expect(strip([at("6", "red", "C", "zz", 41.9)], "red", "C", l)!.off).toBe(1); // not on its path: not placed
  });

  it("draws Purple's trains toward Howard on the Linden-Howard shuttle, not the Express path on past Howard (Oct 12)", () => {
    // 511 at 01:00, southbound to Howard, next South Boulevard: both paths pass it, the Express
    // one on toward Linden (the strip ran Clark/Lake -> Linden and the train went against it)
    const real = drawn as unknown as Lines;
    const t511 = { ...train("511", "p", "5", "Howard", "40840"), lat: 42.03346, lon: -87.67954, hdg: 157, app: true };
    const s = strip([t511], "p", "Howard", real)!;
    expect(real.stations[s.seq[0]].name).toBe("Linden");
    expect(real.stations[s.seq.at(-1)!].name).toBe("Howard");
    expect(s.trains.map((p) => p.t.rn)).toEqual(["511"]);
  });

  it("labels each gap with the poller's minutes to the train ahead, and our own measure where we make a claim", () => {
    const t = (rn: string, x: number, stop: string, more: Partial<Train> = {}) => ({ t: { ...train(rn, "red", "5", "C", "c"), stop, ...more }, x });
    const s = { seq: [], at: [], off: 0, trains: [
      t("1", 0.1, "p2", { last_by: "red:2", ahead_s: 800 }), t("2", 0.4, "p5", { last_by: "red:3", ahead_s: 70 }), t("3", 0.45, "p5"),
    ] };
    expect(gaps(s)).toEqual([{ from: 0.1, to: 0.4, s: 800, long: false }, { from: 0.4, to: 0.45, s: 70, long: false }]);
    // measured from a train that isn't the next one on the strip: no label rather than a wrong one
    const other = { ...s, trains: [t("1", 0.1, "p2", { last_by: "red:9", ahead_s: 800 }), s.trains[1]] };
    expect(gaps(other)).toEqual([]);
    // a long wait (our claim): its own minutes, measured, in red
    const long = { ...s, trains: [t("1", 0.1, "p2", { long_wait: true, gap_s: 1_500, last_by: "red:2", ahead_s: 800 }), s.trains[1]] };
    expect(gaps(long)[0]).toEqual({ from: 0.1, to: 0.4, s: 1_500, long: true });
    // two trains bound for the same platform share one hole: the leading one's, not twice
    const same = { ...s, trains: [t("2", 0.4, "p5", { long_wait: true, gap_s: 900 }), s.trains[2]] };
    expect(gaps(same)).toEqual([]);
    // trains together (our claim): how far behind it ran, measured, not CTA's forecast
    const tog = { ...s, trains: [t("1", 0.1, "p2", { together_with: "red:2", together_s: 70, last_by: "red:2", ahead_s: 800 }), s.trains[1]] };
    expect(gaps(tog)[0]).toEqual({ from: 0.1, to: 0.4, s: 70, long: false, together: true });
  });

  it("marks trains together with the one ahead of them, both", () => {
    const t = (rn: string, x: number, w: string | null) => ({ t: { ...train(rn, "red", "5", "C", "c"), together_with: w }, x });
    expect(together({ seq: [], at: [], off: 0, trains: [t("1", 0.1, null), t("2", 0.2, "red:1"), t("3", 0.5, "red:9")] }))
      .toEqual(new Set(["red:2", "red:1"])); // red:9 isn't on this strip
  });

  it("groups a station's predictions by platform, soonest first", () => {
    const eta = (rn: string, desc: string, arr: number): Eta => ({
      rn, rt: "red", dest: null, stop: "1", stop_desc: desc, arr, t: null, sch: false, app: false, dly: false, flt: false,
    });
    const p = platforms([eta("2", "Service toward Howard", 300), eta("1", "Service toward Howard", 100), eta("3", "Service toward 95th", 200)]);
    expect(p.map((x) => [x.label, x.etas.map((e) => e.rn)])).toEqual([
      ["Service toward 95th", ["3"]], ["Service toward Howard", ["1", "2"]],
    ]);
    // a terminal's arrivals after the platforms riders board at
    const t = platforms([eta("4", "Howard (Red Line Terminal Arrival)", 50), eta("5", "Service toward Linden", 900)]);
    expect(t.map((x) => x.label)).toEqual(["Service toward Linden", "Howard (Red Line Terminal Arrival)"]);
  });
});
