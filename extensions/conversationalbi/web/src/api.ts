// Same-origin calls to the gateway with the session cookie. Writes carry the CSRF header.
export const CSRF = { "X-Iceberg-Cbi": "1" };

export class ApiError extends Error {
  constructor(message: string, readonly code: string, readonly status: number) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(init.body ? { "Content-Type": "application/json" } : {}), ...CSRF, ...init.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(body.error ?? `Request failed (${response.status}).`, body.code ?? "error", response.status);
  return body as T;
}

export type ModelRef = { database: string; namespace: string[]; name: string };

export type ModelEntry = {
  model: ModelRef;
  key: string;
  name: string;
  namespace: string[];
  databaseName: string;
  environment: string;
  shared: boolean;
  teamName: string;
  readingTeamName: string;
};

export type NotEnabled = { team: string; teamName: string; environment: string; canEnable: boolean };

export type Column = {
  name: string;
  kind: "dimension" | "metric";
  label: string;
  unit?: string | null;
  additive?: boolean;
  grain?: string | null;
  description?: string;
  sqlType?: string;
};

export type Result = {
  id: string;
  modelName: string;
  shared: boolean;
  sql: string;
  columns: Column[];
  metrics: { name: string; description: string; expression: string; unit: string | null }[];
  rows: unknown[][];
  rowCount: number;
  truncated: boolean;
  joinPaths: Record<string, string[]>;
  elapsedMs: number;
  dataAsOf?: string | null;
};

export const sameRef = (a?: ModelRef | null, b?: ModelRef | null) =>
  !!a && !!b && a.database === b.database && a.name === b.name && a.namespace.join("\u001f") === b.namespace.join("\u001f");
