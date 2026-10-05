export type DashboardPage = "overview" | "sessions" | "players" | "events" | "modules" | "system";

const pageAliases: Record<string, DashboardPage> = {
  overview: "overview",
  sessions: "sessions",
  players: "players",
  subjects: "players",
  events: "events",
  modules: "modules",
  system: "system",
  systems: "system",
};

/** Hash paths keep the static Dashboard deployable without server route rewrites. */
export function readDashboardPage(hash: string): DashboardPage {
  const path = hash.replace(/^#\/?/, "").split("?", 1)[0]?.replace(/\/$/, "") ?? "";
  return Object.hasOwn(pageAliases, path) ? pageAliases[path]! : "overview";
}

export function dashboardPageHref(page: DashboardPage): string {
  return `#/${page}`;
}
