import { useCallback, useEffect, useRef, useState } from "react";

export type DashboardTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "meccha-dashboard-theme";
const SYSTEM_THEME_QUERY = "(prefers-color-scheme: dark)";

export function readThemePreference(): DashboardTheme | null {
  try {
    const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
    return saved === "light" || saved === "dark" ? saved : null;
  } catch {
    // Private browsing and browser policy can block storage entirely.
    return null;
  }
}

function systemThemeQuery(): MediaQueryList | null {
  try {
    return typeof window.matchMedia === "function" ? window.matchMedia(SYSTEM_THEME_QUERY) : null;
  } catch {
    return null;
  }
}

export function systemTheme(): DashboardTheme {
  const query = systemThemeQuery();
  return query ? query.matches ? "dark" : "light" : "dark";
}

export function applyTheme(theme: DashboardTheme): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

/** Apply the preference before React's first render, without an inline script. */
export function initializeTheme(): DashboardTheme {
  const theme = readThemePreference() ?? systemTheme();
  applyTheme(theme);
  return theme;
}

export function useDashboardTheme() {
  const [initialPreference] = useState(readThemePreference);
  const preferenceRef = useRef<DashboardTheme | null>(initialPreference);
  const [theme, setTheme] = useState<DashboardTheme>(() => initialPreference ?? systemTheme());

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    const query = systemThemeQuery();
    if (!query) return;
    const synchronizeSystemTheme = (matches: boolean) => {
      if (preferenceRef.current !== null) return;
      const nextTheme = matches ? "dark" : "light";
      applyTheme(nextTheme);
      setTheme(nextTheme);
    };
    // Catch a system preference change between startup and effect registration.
    synchronizeSystemTheme(query.matches);
    const onChange = (event: MediaQueryListEvent) => synchronizeSystemTheme(event.matches);
    if (typeof query.addEventListener === "function") {
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    }
    if (typeof query.addListener === "function") {
      query.addListener(onChange);
      return () => query.removeListener(onChange);
    }
  }, []);

  const selectTheme = useCallback((nextTheme: DashboardTheme) => {
    // Keep the explicit choice even if storage is unavailable or full.
    preferenceRef.current = nextTheme;
    applyTheme(nextTheme);
    setTheme(nextTheme);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, nextTheme);
    } catch {
      // The current page remains usable; only persistence is unavailable.
    }
  }, []);

  return { theme, selectTheme };
}
