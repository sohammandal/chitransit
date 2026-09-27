import { describe, expect, it } from "vitest";
import { matchRoutes, toOpen } from "./search";

const names = {
  "6": "Jackson Park Express", "60": "Blue Island/26th", "62": "Archer", "62H": "Archer/Harlem",
  "66": "Chicago", "7": "Harrison", "79": "79th", "49": "Western", "49B": "North Western",
  "X49": "Western Express", "J14": "Jeffery Jump",
};

describe("route search", () => {
  it("6 suggests every route starting with 6 in number order, and opens #6 on a pause", () => {
    expect(matchRoutes("6", names).map((m) => m.rt)).toEqual(["6", "60", "62", "62H", "66"]);
    expect(toOpen("6", names)).toBe("6");
    expect(toOpen("66", names)).toBe("66");
  });

  it("an exact number wins over longer ones (62 vs 62H); a partial with many matches opens nothing", () => {
    expect(toOpen("62", names)).toBe("62");
    expect(toOpen("62h", names)).toBe("62H");
    expect(toOpen("4", names)).toBeNull();
  });

  it("names match from 3 letters, after number matches", () => {
    expect(matchRoutes("western", names).map((m) => m.rt)).toEqual(["49", "49B", "X49"]);
    expect(matchRoutes("we", names)).toEqual([]);
    expect(toOpen("jeff", names)).toBe("J14");
    expect(toOpen("western", names)).toBeNull();
  });

  it("nothing for blank or unknown input", () => {
    expect(matchRoutes("  ", names)).toEqual([]);
    expect(toOpen("999", names)).toBeNull();
  });
});
