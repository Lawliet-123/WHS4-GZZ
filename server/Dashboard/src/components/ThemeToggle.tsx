import { useDashboardTheme } from "../theme";
import { Icon } from "./Icon";

export function ThemeToggle() {
  const { theme, selectTheme } = useDashboardTheme();

  return (
    <div className="theme-toggle" role="group" aria-label="화면 테마">
      <button
        className="theme-toggle-button"
        type="button"
        aria-label="라이트 테마"
        aria-pressed={theme === "light"}
        onClick={() => selectTheme("light")}
      >
        <Icon name="sun" size={15} />
        <span className="theme-toggle-label">라이트</span>
      </button>
      <button
        className="theme-toggle-button"
        type="button"
        aria-label="다크 테마"
        aria-pressed={theme === "dark"}
        onClick={() => selectTheme("dark")}
      >
        <Icon name="moon" size={15} />
        <span className="theme-toggle-label">다크</span>
      </button>
    </div>
  );
}
