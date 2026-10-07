import { useDashboardTheme } from "../theme";
import { Icon } from "./Icon";

export function ThemeToggle() {
  const { theme, selectTheme } = useDashboardTheme();
  const nextTheme = theme === "light" ? "dark" : "light";
  const actionLabel = `${nextTheme === "light" ? "라이트" : "다크"} 모드로 전환`;

  return (
    <button
      className="theme-toggle-button"
      type="button"
      aria-label={actionLabel}
      title={actionLabel}
      onClick={() => selectTheme(nextTheme)}
    >
      <Icon name={theme === "light" ? "sun" : "moon"} size={15} />
      <span className="theme-toggle-label">{theme === "light" ? "라이트" : "다크"}</span>
    </button>
  );
}
