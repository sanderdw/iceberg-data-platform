import { toCsv, formatLabel, formatValue, useResult } from "../results";
import { Frame, Problem } from "./Frame";

const SHOWN = 200;

export function ResultTable({ resultId, title, embedded }: { resultId: string; title?: string; embedded?: boolean }) {
  const { result, error } = useResult(resultId);
  if (error) return <Problem text={error} />;
  if (!result) return embedded ? null : <Frame eyebrow="TABLE" title={title} status="loading" />;
  const download = () => {
    const url = URL.createObjectURL(new Blob([toCsv(result)], { type: "text/csv" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${result.modelName.toLowerCase().replace(/[^a-z0-9]+/g, "-")}-${result.id}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };
  const table = (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {result.columns.map((c) => (
              <th key={c.name} className={c.kind === "metric" ? "num" : undefined} title={c.description || c.name}>
                {c.label}
                {c.unit ? <span className="unit"> {c.unit}</span> : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.slice(0, SHOWN).map((row, r) => (
            <tr key={r}>
              {row.map((value, i) => (
                <td key={i} className={typeof value === "number" ? "num" : undefined}>
                  {result.columns[i].kind === "dimension" ? formatLabel(value, result.columns[i].grain) : formatValue(value)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {result.rowCount > SHOWN && <p className="hint">Showing {SHOWN} of {result.rowCount} rows. Download the CSV for all.</p>}
    </div>
  );
  if (embedded) return table;
  return (
    <Frame eyebrow={`TABLE · ${result.modelName}`} title={title} result={result}
      actions={<button type="button" className="quiet" onClick={download}>CSV</button>}>
      {table}
    </Frame>
  );
}
