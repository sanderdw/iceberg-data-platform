import {
  BarController,
  BarElement,
  CategoryScale,
  Chart,
  type ChartConfiguration,
  Filler,
  LinearScale,
  LineController,
  LineElement,
  PointElement,
  ScatterController,
  Tooltip,
  Legend,
} from "chart.js";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Result } from "../api";
import { columnIndex, formatLabel, formatValue, useResult } from "../results";
import { baseOptions, ink, series, type Theme, useTheme } from "../theme";
import { Frame, Problem } from "./Frame";
import { ResultTable } from "./ResultTable";

Chart.register(BarController, BarElement, LineController, LineElement, PointElement, ScatterController, CategoryScale, LinearScale, Tooltip, Legend, Filler);

export type ChartKind = "bar" | "line" | "area" | "scatter";
export type ChartProps = {
  resultId: string; kind: ChartKind; x: string; y: string[]; series?: string; title?: string; horizontal?: boolean; stacked?: boolean;
};

const MAX_SERIES = 6;

function rgba(hex: string, alpha: number) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

// Build the Chart.js configuration from a result and the columns the agent named.
export function chartConfig(result: Result, props: ChartProps, theme: Theme = "dark"): { config?: ChartConfiguration; problem?: string; note?: string; height?: number } {
  const xi = columnIndex(result, props.x);
  const yi = props.y.map((name) => columnIndex(result, name));
  const si = props.series ? columnIndex(result, props.series) : -1;
  const missing = [props.x, ...props.y, props.series].filter((name): name is string => !!name && columnIndex(result, name) < 0);
  if (missing.length) return { problem: `This result has no column ${missing.join(", ")}.` };
  if (yi.some((i) => result.rows.some((r) => r[i] !== null && typeof r[i] !== "number"))) {
    return { problem: "A chart's values must be numbers; use show_table for this result." };
  }
  const xColumn = result.columns[xi];
  const units = [...new Set(yi.map((i) => result.columns[i].unit).filter(Boolean))];
  const options = baseOptions(units.length === 1 ? String(units[0]) : "", theme);
  const numeric = (v: unknown) => (typeof v === "number" ? v : null);
  let note: string | undefined;
  type Line = { label: string; values: (number | null)[] };
  let labels: string[];
  let lines: Line[];
  if (props.kind === "scatter") {
    if (result.rows.some((r) => r[xi] !== null && typeof r[xi] !== "number")) return { problem: "A scatter chart needs a numeric x column." };
    // A row without both coordinates is not a point; plotting it at 0 would invent data.
    const data = result.rows.flatMap((r) => { const x = numeric(r[xi]), y = numeric(r[yi[0]]); return x === null || y === null ? [] : [{ x, y }]; });
    const s = series(0, theme);
    return {
      config: {
        type: "scatter",
        data: { datasets: [{ label: result.columns[yi[0]].label, data, backgroundColor: rgba(s.color, 0.8), pointRadius: 3 }] },
        options: { ...options, scales: { ...options.scales, x: { ...options.scales!.x, type: "linear" } } } as ChartConfiguration["options"],
      },
    };
  }
  if (si >= 0) {
    labels = [...new Set(result.rows.map((r) => formatLabel(r[xi], xColumn.grain)))];
    const totals = new Map<string, number>();
    for (const r of result.rows) totals.set(formatValue(r[si]), (totals.get(formatValue(r[si])) ?? 0) + (numeric(r[yi[0]]) ?? 0));
    const keys = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
    if (keys.length > MAX_SERIES) note = `Showing the ${MAX_SERIES} largest of ${keys.length} ${result.columns[si].label} values.`;
    lines = keys.slice(0, MAX_SERIES).map((key) => {
      const byLabel = new Map(result.rows.filter((r) => formatValue(r[si]) === key).map((r) => [formatLabel(r[xi], xColumn.grain), numeric(r[yi[0]])]));
      return { label: key, values: labels.map((l) => byLabel.get(l) ?? null) };
    });
  } else {
    labels = result.rows.map((r) => formatLabel(r[xi], xColumn.grain));
    lines = yi.map((i) => ({ label: result.columns[i].label, values: result.rows.map((r) => numeric(r[i])) }));
  }
  const type = props.kind === "bar" ? "bar" : "line";
  // Stacked bars add values up, so only metrics that add up (a SUM or COUNT) stack; averages never do.
  const unstackable = yi.map((i) => result.columns[i]).filter((c) => !c.additive);
  const stacked = type === "bar" && !!props.stacked && lines.length > 1 && unstackable.length === 0;
  if (type === "bar" && props.stacked && unstackable.length) {
    note = [note, `Bars side by side: ${unstackable.map((c) => c.label).join(", ")} does not add up across groups.`].filter(Boolean).join(" ");
  }
  const horizontal = type === "bar" && !!props.horizontal;
  const datasets = lines.map((line, index) => {
    const s = series(index, theme);
    return type === "bar"
      ? {
          label: line.label, data: line.values, backgroundColor: rgba(s.color, s.fill), maxBarThickness: 48,
          // A hairline in the card's colour keeps stacked segments of neighbouring greys apart.
          borderWidth: stacked ? 1 : 0, borderColor: ink(theme).surface,
        }
      : {
          label: line.label, data: line.values, borderColor: s.color, borderDash: s.dash, borderWidth: 1.5, spanGaps: true,
          pointRadius: labels.length <= 31 ? 2 : 0, pointBackgroundColor: s.color, tension: 0,
          fill: props.kind === "area" ? "origin" : false, backgroundColor: rgba(s.color, 0.08),
        };
  });
  if (lines.length > 1) options.plugins!.legend!.display = true;
  const category = { ...options.scales!.x, stacked };
  const value = { ...options.scales!.y, stacked };
  options.scales = { x: category, y: value };
  let height: number | undefined;
  if (horizontal) {
    // Categories run down the side, so long names stay readable; the value axis and its grid move to the bottom.
    options.indexAxis = "y";
    options.interaction = { ...options.interaction, axis: "y" };
    options.scales = { x: value, y: category };
    height = Math.max(300, labels.length * (stacked ? 1 : lines.length) * 24 + 64);
  }
  return { config: { type, data: { labels, datasets }, options } as ChartConfiguration, note, height };
}

export function ResultChart(props: Partial<ChartProps>) {
  // While the agent is still streaming the tool call, its arguments arrive in pieces.
  if (!props.resultId || !props.kind || !props.x || !props.y?.length) return <Frame eyebrow="CHART" title={props.title} status="loading" />;
  return <LoadedChart {...(props as ChartProps)} />;
}

function LoadedChart(props: ChartProps) {
  const { result, error } = useResult(props.resultId);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [showData, setShowData] = useState(false);
  const [theme] = useTheme();
  const built = useMemo(() => (result ? chartConfig(result, props, theme) : undefined), [result, props.kind, props.x, props.y.join(), props.series, props.horizontal, props.stacked, theme]);

  useEffect(() => {
    if (!built?.config || !canvas.current) return;
    let chart: Chart | undefined;
    let live = true;
    // Canvas text uses the fonts at draw time: wait until Space Mono is loaded.
    document.fonts.ready.then(() => {
      if (live && canvas.current) chart = new Chart(canvas.current, built.config!);
    });
    return () => {
      live = false;
      chart?.destroy();
    };
  }, [built]);

  if (error) return <Problem text={error} />;
  if (!result || !built) return <Frame eyebrow="CHART" title={props.title} status="loading" />;
  if (built.problem) return <Problem text={built.problem} />;
  const summary = `${props.title ?? "Chart"}: ${props.y.join(", ")} by ${props.x}${props.series ? ` and ${props.series}` : ""}, ${result.rowCount} rows.`;
  return (
    <Frame eyebrow={`CHART · ${result.modelName}`} title={props.title} result={result} note={built.note}
      actions={<button type="button" className="quiet" onClick={() => setShowData(!showData)}>{showData ? "HIDE DATA" : "SHOW DATA"}</button>}>
      <div className="chart-box" style={built.height ? { height: built.height } : undefined}>
        <canvas ref={canvas} role="img" aria-label={summary} />
      </div>
      {showData && <ResultTable resultId={props.resultId} embedded />}
    </Frame>
  );
}
