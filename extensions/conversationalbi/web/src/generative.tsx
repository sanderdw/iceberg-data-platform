import { useComponent, useDefaultRenderTool, useRenderTool } from "@copilotkit/react-core/v2";
import { z } from "zod";
import { QueryCard, type QueryParameters } from "./components/QueryCard";
import { ResultChart } from "./components/ResultChart";
import { ResultKpi } from "./components/ResultKpi";
import { ResultTable } from "./components/ResultTable";
import { RESULT_ID } from "./results";

const resultId = z.string().regex(RESULT_ID).describe("The resultId that query returned.");
const column = z.string().max(300).describe("A column name exactly as query returned it, such as CARRIER.name or a metric name.");

const TOOL_LABELS: Record<string, string> = {
  list_models: "Looking up the models you can ask about",
  select_model: "Selecting a model",
  describe_model: "Reading the model's datasets, fields and metrics",
};

// Generative UI: the agent picks a component and a result; the component loads the numbers itself.
export function useGenerativeUI() {
  useComponent({
    name: "show_chart",
    description:
      "Chart a query result, normally with the arguments in query's `recommended` field. kind: bar for comparing " +
      "categories, line or area for a time grain, scatter for two metrics. x is a dimension column (or a metric for " +
      "scatter), y one to four metric columns, series an optional second dimension column that splits one metric into " +
      "lines or bars. horizontal draws bars sideways, for many or long category names; stacked stacks a bar's series " +
      "(only metrics that add up, such as counts and sums, stack).",
    parameters: z.object({
      resultId,
      kind: z.enum(["bar", "line", "area", "scatter"]),
      x: column,
      y: z.array(column).min(1).max(4),
      series: column.optional(),
      horizontal: z.boolean().optional(),
      stacked: z.boolean().optional(),
      title: z.string().max(120).optional().describe("A short title that states what is measured and over what."),
    }),
    render: ResultChart,
  });
  useComponent({
    name: "show_table",
    description: "Show a query result as a table, for detail or when there are too many categories to chart.",
    parameters: z.object({ resultId, title: z.string().max(120).optional() }),
    render: ResultTable,
  });
  useComponent({
    name: "show_kpi",
    description: "Show one number from a single-row query result, large, with its unit and definition.",
    parameters: z.object({ resultId, column, label: z.string().max(80).optional() }),
    render: ResultKpi,
  });
  useRenderTool({
    name: "query",
    parameters: z.object({
      metrics: z.array(z.string()).optional(),
      dimensions: z.array(z.object({ field: z.string(), via: z.array(z.string()).optional(), grain: z.string().nullish() })).optional(),
      filters: z.array(z.object({ field: z.string().nullish(), metric: z.string().nullish(), op: z.string(), value: z.unknown().optional() })).optional(),
      limit: z.number().optional(),
    }),
    render: ({ status, parameters, result }) => (
      <QueryCard status={status} parameters={(parameters ?? {}) as QueryParameters} result={result} />
    ),
  });
  useDefaultRenderTool({
    render: ({ name, status }) => (
      <p className="tool-line">
        <span className="eyebrow">{status === "complete" ? "DONE" : "…"}</span> {TOOL_LABELS[name] ?? name}
      </p>
    ),
  });
}
