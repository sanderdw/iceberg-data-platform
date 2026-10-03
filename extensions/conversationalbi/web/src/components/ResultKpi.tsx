import { columnIndex, formatValue, useResult } from "../results";
import { Frame, Problem } from "./Frame";

// One number, large: the hero moment of the Nothing style (Doto), with its definition beside it.
export function ResultKpi({ resultId, column, label }: { resultId: string; column: string; label?: string }) {
  const { result, error } = useResult(resultId);
  if (error) return <Problem text={error} />;
  if (!result) return <Frame eyebrow="KPI" status="loading" />;
  const index = columnIndex(result, column);
  if (index < 0) return <Problem text={`This result has no column ${column}.`} />;
  if (result.rowCount !== 1) return <Problem text="A KPI shows one number; this result has several rows. Use a chart or a table." />;
  const meta = result.columns[index];
  const metric = result.metrics.find((m) => m.name === column);
  return (
    <Frame eyebrow={`KPI · ${result.modelName}`} result={result}>
      <div className="kpi">
        <span className="kpi-value">{formatValue(result.rows[0][index])}</span>
        {meta.unit && <span className="kpi-unit">{meta.unit.toUpperCase()}</span>}
      </div>
      <p className="kpi-label">{label ?? meta.label}</p>
      {metric?.description && <p className="hint">{metric.description}</p>}
    </Frame>
  );
}
