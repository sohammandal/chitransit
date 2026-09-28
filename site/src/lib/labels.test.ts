import { describe, expect, it } from "vitest";
import { crossStreet, fitEnds, ownStreet, place, placeName } from "./labels";

describe("street labels", () => {
  const clark = ["Clark & Harrison", "Clark & Belmont", "Belmont & Clark", "Clark & Diversey", "Dearborn & Adams"];

  it("finds the route's own street and shortens names to the cross street", () => {
    expect(ownStreet(clark)).toBe("clark");
    expect(crossStreet("Clark & Belmont", "clark")).toBe("Belmont");
    expect(crossStreet("Belmont & Clark", "clark")).toBe("Belmont");
    expect(crossStreet("Dearborn & Adams", "clark")).toBe("Dearborn & Adams"); // off the route's street
    expect(crossStreet("Navy Pier Terminal", "chicago")).toBe("Navy Pier Terminal");
  });

  it("places the most important labels first and never lets two overlap", () => {
    const placed = place(
      [
        { x: 100, text: "Belmont", weight: 3 },
        { x: 110, text: "Diversey", weight: 2 }, // would overlap Belmont: dropped
        { x: 200, text: "Fullerton", weight: 2 },
        { x: 5, text: "Harrison", weight: 5 }, // would spill past the left edge: dropped
      ],
      14, 326, 6,
    );
    expect(placed.map((p) => p.text)).toEqual(["Belmont", "Fullerton"]);
    for (let i = 1; i < placed.length; i++) expect(placed[i].from).toBeGreaterThanOrEqual(placed[i - 1].to + 8);
  });

  it("list rows drop the route's own street when the route is named after it", () => {
    expect(placeName("Cottage Grove & 60th Street", "Cottage Grove")).toBe("60th Street");
    expect(placeName("Lake Shore & Waveland", "Inner Lake Shore/Michigan Express")).toBe("Waveland");
    expect(placeName("State & Grand (Red Line)", "Broadway")).toBe("State & Grand (Red Line)");
    expect(placeName("Navy Pier Terminal", "Chicago")).toBe("Navy Pier Terminal");
  });

  it("caps the number of labels", () => {
    const many = Array.from({ length: 10 }, (_, i) => ({ x: 20 + i * 30, text: "Ab", weight: 1 }));
    expect(place(many, 0, 340, 6, 8, 4)).toHaveLength(4);
  });
});

describe("fitEnds", () => {
  const w = (t: string) => t.length * 6; // 6 px a character
  it("keeps both names in full when they fit", () => {
    expect(fitEnds("Chicago & Austin Terminal", "Harrison & Canal", 312, 30, w)).toEqual(["Chicago & Austin Terminal", "Harrison & Canal"]);
  });
  it("shortens the longer name first", () => {
    const [a, b] = fitEnds("Ashland & 95th Street Terminal", "Ravenswood Metra Station", 330, 30, w);
    expect(b).toBe("Ravenswood Metra Station");
    expect(a.endsWith("…")).toBe(true);
    expect(w(a) + w(b) + 30).toBeLessThanOrEqual(330);
  });
  it("shortens both when one alone isn't enough", () => {
    const [a, b] = fitEnds("A".repeat(40), "B".repeat(40), 300, 30, w);
    expect(a.endsWith("…") && b.endsWith("…")).toBe(true);
    expect(w(a) + w(b) + 30).toBeLessThanOrEqual(300);
  });
});
