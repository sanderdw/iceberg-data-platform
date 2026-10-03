import { describe, expect, it } from "vitest";
import type { Result } from "./api";
import { chartConfig } from "./components/ResultChart";
import { formatAsOf, formatLabel, toCsv } from "./results";

const result: Result = {
  id: "res-" + "a".repeat(24), modelName: "Flights", shared: true, sql: "SELECT 1", elapsedMs: 5, truncated: false,
  joinPaths: {}, rowCount: 4,
  columns: [
    { name: "FLIGHT.date:week", kind: "dimension", label: "date (week)", grain: "week" },
    { name: "CARRIER.name", kind: "dimension", label: "name" },
    { name: "average_departure_delay", kind: "metric", label: "average departure delay", unit: "minutes" },
  ],
  metrics: [{ name: "average_departure_delay", description: "Mean delay. Unit: minutes.", expression: "avg(x)", unit: "minutes" }],
  rows: [["2026-01-05", "A", 10], ["2026-01-05", "B", 12.5], ["2026-01-12", "A", 9], ["2026-01-12", "B", null]],
};

describe("charts bind to result columns, never to numbers from the LLM", () => {
  it("builds one series per metric", () => {
    const { config } = chartConfig(result, { resultId: result.id, kind: "bar", x: "CARRIER.name", y: ["average_departure_delay"] });
    expect(config?.type).toBe("bar");
    expect(config?.data.datasets[0].data).toEqual([10, 12.5, 9, null]);
  });

  it("pivots a second dimension into series", () => {
    const { config } = chartConfig(result, {
      resultId: result.id, kind: "line", x: "FLIGHT.date:week", y: ["average_departure_delay"], series: "CARRIER.name",
    });
    expect(config?.data.labels).toEqual(["JAN 05", "JAN 12"]);
    expect(config?.data.datasets.map((d) => [d.label, d.data])).toEqual([["A", [10, 9]], ["B", [12.5, null]]]);
  });

  it("refuses columns the result does not have, and text as values", () => {
    expect(chartConfig(result, { resultId: result.id, kind: "bar", x: "CARRIER.name", y: ["invented"] }).problem)
      .toMatch(/no column invented/);
    expect(chartConfig(result, { resultId: result.id, kind: "bar", x: "FLIGHT.date:week", y: ["CARRIER.name"] }).problem)
      .toMatch(/must be numbers/);
  });
});

describe("exports", () => {
  it("formats grains", () => {
    expect(formatLabel("2026-03-01", "month")).toBe("MAR 2026");
    expect(formatLabel("2026-04-01", "quarter")).toBe("Q2 2026");
  });

  it("never lets a spreadsheet evaluate a value", () => {
    const csv = toCsv({ ...result, rows: [["=HYPERLINK(\"x\")", "B", 1]] });
    expect(csv.split("\n")[1]).toBe(`"'=HYPERLINK(""x"")",B,1`);
  });
});

describe("chart variants", () => {
  const counts: Result = {
    ...result,
    columns: [
      { name: "AIRPORT.name", kind: "dimension", label: "name" },
      { name: "CARRIER.name", kind: "dimension", label: "name" },
      { name: "flights", kind: "metric", label: "flights", additive: true },
      { name: "average_departure_delay", kind: "metric", label: "average departure delay", unit: "minutes" },
    ],
    rows: [["Amsterdam Schiphol", "A", 3, 10], ["Amsterdam Schiphol", "B", 4, 12], ["Rotterdam The Hague", "A", 2, 8]],
  };

  it("draws horizontal bars with the value axis at the bottom", () => {
    const { config, height } = chartConfig(counts, { resultId: counts.id, kind: "bar", x: "AIRPORT.name", y: ["flights"], horizontal: true });
    const options = config?.options as { indexAxis?: string; scales: Record<string, { grid?: { display?: boolean } }> };
    expect(options.indexAxis).toBe("y");
    expect(options.scales.y.grid?.display).toBe(false);
    expect(height).toBeGreaterThanOrEqual(300);
  });

  it("stacks metrics that add up", () => {
    const { config, note } = chartConfig(counts, { resultId: counts.id, kind: "bar", x: "AIRPORT.name", y: ["flights"], series: "CARRIER.name", stacked: true });
    const scales = (config?.options as { scales: Record<string, { stacked?: boolean }> }).scales;
    expect([scales.x.stacked, scales.y.stacked]).toEqual([true, true]);
    expect(note).toBeUndefined();
  });

  it("never stacks averages", () => {
    const { config, note } = chartConfig(counts, {
      resultId: counts.id, kind: "bar", x: "AIRPORT.name", y: ["average_departure_delay"], series: "CARRIER.name", stacked: true,
    });
    expect((config?.options as { scales: Record<string, { stacked?: boolean }> }).scales.y.stacked).toBe(false);
    expect(note).toMatch(/does not add up/);
  });
});

describe("freshness", () => {
  it("dates an answer by its snapshot, in UTC", () => {
    expect(formatAsOf("2026-09-01T08:00:00Z")).toBe("01 SEP 2026 08:00 UTC");
    expect(formatAsOf(null)).toBeUndefined();
  });
});
