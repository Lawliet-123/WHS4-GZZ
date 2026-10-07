// @vitest-environment jsdom

import { StrictMode } from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { initializeTheme, readThemePreference, THEME_STORAGE_KEY } from "../theme";
import { ThemeToggle } from "./ThemeToggle";

type ThemeListener = (event: MediaQueryListEvent) => void;

function mockSystemTheme(dark: boolean, legacy = false) {
  const listeners = new Set<ThemeListener>();
  const add = vi.fn((_type: string, listener: ThemeListener) => listeners.add(listener));
  const remove = vi.fn((_type: string, listener: ThemeListener) => listeners.delete(listener));
  const addLegacy = vi.fn((listener: ThemeListener) => listeners.add(listener));
  const removeLegacy = vi.fn((listener: ThemeListener) => listeners.delete(listener));
  const query = {
    matches: dark,
    media: "(prefers-color-scheme: dark)",
    addEventListener: legacy ? undefined : add,
    removeEventListener: legacy ? undefined : remove,
    addListener: addLegacy,
    removeListener: removeLegacy,
  };
  vi.stubGlobal("matchMedia", vi.fn(() => query));
  return {
    add, remove, addLegacy, removeLegacy, listeners,
    change(nextDark: boolean) {
      query.matches = nextDark;
      act(() => listeners.forEach((listener) => listener({ matches: nextDark } as MediaQueryListEvent)));
    },
  };
}

function expectTheme(theme: "light" | "dark") {
  expect(document.documentElement.dataset.theme).toBe(theme);
  expect(document.documentElement.style.colorScheme).toBe(theme);
  expect(screen.getByRole("button", { name: "라이트 테마" }).getAttribute("aria-pressed")).toBe(String(theme === "light"));
  expect(screen.getByRole("button", { name: "다크 테마" }).getAttribute("aria-pressed")).toBe(String(theme === "dark"));
}

beforeEach(() => {
  window.localStorage.removeItem(THEME_STORAGE_KEY);
  delete document.documentElement.dataset.theme;
  document.documentElement.style.removeProperty("color-scheme");
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  window.localStorage.removeItem(THEME_STORAGE_KEY);
  delete document.documentElement.dataset.theme;
  document.documentElement.style.removeProperty("color-scheme");
});

describe("dashboard theme", () => {
  it.each([true, false])("follows the initial system preference when no choice is saved (dark=%s)", (dark) => {
    mockSystemTheme(dark);
    render(<ThemeToggle />);
    expectTheme(dark ? "dark" : "light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it.each(["light", "dark"] as const)("restores the saved %s choice instead of the system theme", (theme) => {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    const system = mockSystemTheme(theme === "light");
    render(<ThemeToggle />);
    expectTheme(theme);
    system.change(theme === "light");
    expectTheme(theme);
  });

  it.each(["", "system", "LIGHT", "null", "<script>"])("ignores invalid saved preferences: %s", (invalid) => {
    window.localStorage.setItem(THEME_STORAGE_KEY, invalid);
    mockSystemTheme(false);
    expect(readThemePreference()).toBeNull();
    render(<ThemeToggle />);
    expectTheme("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe(invalid);
  });

  it("applies a saved theme synchronously before React renders", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    mockSystemTheme(true);
    expect(initializeTheme()).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(document.documentElement.style.colorScheme).toBe("light");
    expect(screen.queryByRole("group", { name: "화면 테마" })).toBeNull();
  });

  it("tracks system changes only until an explicit choice is made", () => {
    const system = mockSystemTheme(false);
    render(<ThemeToggle />);
    expectTheme("light");
    system.change(true);
    expectTheme("dark");
    fireEvent.click(screen.getByRole("button", { name: "라이트 테마" }));
    expectTheme("light");
    system.change(true);
    expectTheme("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("persists selections and restores them after remount without API calls", () => {
    mockSystemTheme(false);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const first = render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: "다크 테마" }));
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    first.unmount();
    render(<ThemeToggle />);
    expectTheme("dark");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps changing the current theme when the localStorage getter is blocked", () => {
    const system = mockSystemTheme(true);
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => { throw new DOMException("Blocked", "SecurityError"); });
    expect(() => initializeTheme()).not.toThrow();
    render(<ThemeToggle />);
    expectTheme("dark");
    fireEvent.click(screen.getByRole("button", { name: "라이트 테마" }));
    expectTheme("light");
    system.change(true);
    expectTheme("light");
  });

  it("keeps the explicit choice when saving exceeds storage quota", () => {
    const system = mockSystemTheme(true);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Full", "QuotaExceededError"); });
    render(<ThemeToggle />);
    fireEvent.click(screen.getByRole("button", { name: "라이트 테마" }));
    expectTheme("light");
    system.change(true);
    expectTheme("light");
  });

  it("falls back to dark without matchMedia and still permits both choices", () => {
    vi.stubGlobal("matchMedia", undefined);
    render(<ThemeToggle />);
    expectTheme("dark");
    fireEvent.click(screen.getByRole("button", { name: "라이트 테마" }));
    expectTheme("light");
    fireEvent.click(screen.getByRole("button", { name: "다크 테마" }));
    expectTheme("dark");
  });

  it("handles an unavailable system preference API without breaking startup", () => {
    vi.stubGlobal("matchMedia", () => { throw new Error("Unavailable"); });
    expect(initializeTheme()).toBe("dark");
    render(<ThemeToggle />);
    expectTheme("dark");
  });

  it("uses the legacy media listener API and removes it on unmount", () => {
    const system = mockSystemTheme(false, true);
    const view = render(<ThemeToggle />);
    expect(system.addLegacy).toHaveBeenCalledOnce();
    system.change(true);
    expectTheme("dark");
    view.unmount();
    expect(system.removeLegacy).toHaveBeenCalledOnce();
    expect(system.listeners.size).toBe(0);
  });

  it("cleans up modern system listeners under StrictMode", () => {
    const system = mockSystemTheme(true);
    const view = render(<StrictMode><ThemeToggle /></StrictMode>);
    expect(system.listeners.size).toBe(1);
    view.unmount();
    expect(system.listeners.size).toBe(0);
    expect(system.remove).toHaveBeenCalledTimes(system.add.mock.calls.length);
  });

  it("provides focusable native buttons with stable names and pressed state", () => {
    mockSystemTheme(false);
    render(<ThemeToggle />);
    expect(screen.getByRole("group", { name: "화면 테마" })).toBeTruthy();
    const dark = screen.getByRole("button", { name: "다크 테마" }) as HTMLButtonElement;
    expect(dark.type).toBe("button");
    expect(dark.tabIndex).toBe(0);
    dark.focus();
    expect(document.activeElement).toBe(dark);
    fireEvent.click(dark);
    expectTheme("dark");
    expect(screen.getByRole("button", { name: "다크 테마" })).toBe(dark);
  });
});
