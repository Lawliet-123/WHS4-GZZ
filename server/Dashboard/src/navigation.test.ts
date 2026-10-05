import { describe, expect, it } from "vitest";
import { dashboardPageHref, readDashboardPage, type DashboardPage } from "./navigation";

describe("Dashboard hash navigation", () => {
  it.each<DashboardPage>(["overview", "sessions", "players", "events", "modules", "system"])(
    "round-trips the %s page",
    (page) => {
      expect(dashboardPageHref(page)).toBe(`#/${page}`);
      expect(readDashboardPage(dashboardPageHref(page))).toBe(page);
    },
  );

  it.each([
    ["#subjects", "players"],
    ["#/subjects", "players"],
    ["#systems", "system"],
    ["#overview", "overview"],
    ["#sessions", "sessions"],
    ["#events", "events"],
    ["#modules", "modules"],
  ] as const)("keeps the legacy %s link working", (hash, expected) => {
    expect(readDashboardPage(hash)).toBe(expected);
  });

  it("does not treat query parameters as a page name", () => {
    expect(readDashboardPage("#/players?session_id=session_001")).toBe("players");
    expect(readDashboardPage("#events?module=esp")).toBe("events");
    expect(readDashboardPage("#/modules/")).toBe("modules");
  });

  it.each(["", "#", "#/", "#/unknown", "#/players/detail", "#/__proto__", "#constructor"])(
    "falls back safely for %s",
    (hash) => expect(readDashboardPage(hash)).toBe("overview"),
  );
});
