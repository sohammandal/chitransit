import { describe, expect, it } from "vitest";
import { ctaLink, plainClick, routeUrl } from "./links";

describe("links", () => {
  it("leaves new-tab clicks to the browser", () => {
    const click = (o: object) => plainClick({ button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...o } as MouseEvent);
    expect(click({ button: 0 })).toBe(true);
    for (const o of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
      expect(click({ button: 0, ...o })).toBe(false);
    }
  });

  it("gives each route its address", () => {
    expect(routeUrl("66")).toBe("/route/66/");
    expect(routeUrl("J14")).toBe("/route/J14/");
    expect(routeUrl(null)).toBe("/");
  });

  it("links an alert only to CTA's own site (Oct 11: the feed passes CTA's link through)", () => {
    const cta = "https://www.transitchicago.com/travel_information/alert_detail.aspx?AlertId=117779";
    expect(ctaLink(cta)).toBe(cta);
    expect(ctaLink("https://transitchicago.com/alerts/")).toBe("https://transitchicago.com/alerts/");
    for (const u of [
      "http://www.transitchicago.com/alerts/", // not https (the poller upgrades CTA's http links)
      "https://www.transitchicago.com.example.net/", // CTA's name at the start of someone else's
      "https://example.net/www.transitchicago.com",
      "https://www.transitchicago.com@example.net/", // a user name before someone else's host
      "https://faketransitchicago.com/",
      "javascript:alert(1)",
      "data:text/html,hi",
      "not a link",
      "",
      null,
      undefined,
    ]) {
      expect(ctaLink(u)).toBeNull();
    }
  });
});
