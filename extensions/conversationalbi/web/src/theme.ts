// Light and dark as equals, like the platform portals; Chart.js in the Nothing style: monochrome
// series told apart by opacity and dash pattern, Space Mono ticks, hairline grid, no animation.
import type { ChartOptions } from "chart.js";
import { useEffect, useState } from "react";

export type Theme = "dark" | "light";
const KEY = "iceberg-theme";

const INKS = {
  dark: { display: "#ffffff", primary: "#e8e8e8", secondary: "#999999", disabled: "#666666", border: "#222222",
          borderVisible: "#333333", surface: "#111111", series: ["#e8e8e8", "#999999", "#666666", "#444444"] },
  light: { display: "#000000", primary: "#1a1a1a", secondary: "#666666", disabled: "#999999", border: "#e8e8e8",
           borderVisible: "#cccccc", surface: "#ffffff", series: ["#1a1a1a", "#666666", "#999999", "#cccccc"] },
};
const DASHES: number[][] = [[], [6, 4], [2, 3], [10, 3, 2, 3], [1, 2], [8, 8]];

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function applyTheme(theme: Theme, remember = true) {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.classList.toggle("dark", theme === "dark"); // CopilotKit's dark variant
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#f5f5f5" : "#000000");
  if (remember) {
    try {
      localStorage.setItem(KEY, theme);
    } catch {
      /* The choice lasts for this page only. */
    }
  }
  window.dispatchEvent(new Event("cbi-theme"));
}

export function initialTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  useEffect(() => {
    const update = () => setTheme(currentTheme());
    window.addEventListener("cbi-theme", update);
    return () => window.removeEventListener("cbi-theme", update);
  }, []);
  return [theme, () => applyTheme(theme === "dark" ? "light" : "dark")];
}

export function ink(theme: Theme) {
  return INKS[theme];
}

export function series(index: number, theme: Theme) {
  const colors = INKS[theme].series;
  return { color: colors[index % colors.length], dash: DASHES[index % DASHES.length], fill: index < 4 ? 1 - index * 0.22 : 0.3 };
}

export const MONO = "'Space Mono', ui-monospace, monospace";

export function baseOptions(yLabel: string, theme: Theme): ChartOptions {
  const c = INKS[theme];
  const ticks = { color: c.secondary, font: { family: MONO, size: 10 }, maxRotation: 0, autoSkipPadding: 12 };
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "index", intersect: false },
    layout: { padding: { top: 8, right: 8 } },
    scales: {
      x: { ticks, grid: { display: false }, border: { color: c.borderVisible } },
      y: {
        ticks,
        grid: { color: c.border },
        border: { display: false },
        title: { display: !!yLabel, text: yLabel.toUpperCase(), color: c.disabled, font: { family: MONO, size: 10 } },
      },
    },
    plugins: {
      legend: {
        display: false,
        labels: { color: c.secondary, font: { family: MONO, size: 10 }, boxWidth: 10, boxHeight: 10 },
      },
      tooltip: {
        backgroundColor: c.surface,
        borderColor: c.borderVisible,
        borderWidth: 1,
        cornerRadius: 4,
        titleColor: c.display,
        bodyColor: c.primary,
        titleFont: { family: MONO, size: 11 },
        bodyFont: { family: MONO, size: 11 },
        padding: 10,
        boxPadding: 4,
      },
    },
  };
}
