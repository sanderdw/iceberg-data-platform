import { useEffect, useState } from "react";
import { api, type Result } from "./api";

// Results load by id from the gateway, which re-checks access. The LLM only ever passes the id.
const cache = new Map<string, Promise<Result>>();

export const RESULT_ID = /^res-[a-f0-9]{24}$/;

export function loadResult(id: string): Promise<Result> {
  if (!RESULT_ID.test(id)) return Promise.reject(new Error("This result id is not valid."));
  if (!cache.has(id)) {
    const request = api<Result>(`/api/v1/results/${id}`);
    request.catch(() => cache.delete(id));
    cache.set(id, request);
  }
  return cache.get(id)!;
}

export type ResultState = { result?: Result; error?: string };

export function useResult(id?: string): ResultState {
  const [state, setState] = useState<ResultState>({});
  useEffect(() => {
    if (!id) return;
    let live = true;
    setState({});
    loadResult(id).then(
      (result) => live && setState({ result }),
      (error: Error) => live && setState({ error: error.message }),
    );
    return () => {
      live = false;
    };
  }, [id]);
  return state;
}

export function columnIndex(result: Result, name: string): number {
  return result.columns.findIndex((c) => c.name === name);
}

const numbers = new Intl.NumberFormat("en", { maximumFractionDigits: 2 });

export function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return numbers.format(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

export function formatLabel(value: unknown, grain?: string | null): string {
  if (grain && typeof value === "string" && /^\d{4}-\d{2}-\d{2}/.test(value)) {
    const date = new Date(value.slice(0, 10) + "T00:00:00Z");
    const month = date.toLocaleString("en", { month: "short", timeZone: "UTC" }).toUpperCase();
    if (grain === "year") return String(date.getUTCFullYear());
    if (grain === "quarter") return `Q${Math.floor(date.getUTCMonth() / 3) + 1} ${date.getUTCFullYear()}`;
    if (grain === "month") return `${month} ${date.getUTCFullYear()}`;
    return `${month} ${String(date.getUTCDate()).padStart(2, "0")}`;
  }
  return formatValue(value);
}

// "01 SEP 2026 08:00 UTC": when the metrics' dataset last changed, from the snapshot the query read.
export function formatAsOf(iso?: string | null): string | undefined {
  if (!iso) return undefined;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return undefined;
  const pad = (n: number) => String(n).padStart(2, "0");
  const month = date.toLocaleString("en", { month: "short", timeZone: "UTC" }).toUpperCase();
  return `${pad(date.getUTCDate())} ${month} ${date.getUTCFullYear()} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())} UTC`;
}

export function toCsv(result: Result): string {
  const cell = (v: unknown) => {
    const text = v === null || v === undefined ? "" : String(v);
    // Leading formula characters are escaped so spreadsheets never evaluate a value.
    const safe = /^[=+\-@\t\r]/.test(text) ? "'" + text : text;
    return /[",\n]/.test(safe) ? `"${safe.replaceAll('"', '""')}"` : safe;
  };
  return [result.columns.map((c) => cell(c.name)), ...result.rows.map((r) => r.map(cell))].map((r) => r.join(",")).join("\n");
}
