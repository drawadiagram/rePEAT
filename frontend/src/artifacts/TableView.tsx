/** A sortable ranked table, used for the design ensemble. */
import { useMemo, useState } from "react";

interface TableData {
  metric?: string;
  direction?: string;
  rows?: Record<string, unknown>[];
}

export default function TableView({ data }: { data: TableData | null }) {
  const rows = data?.rows ?? [];
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [descending, setDescending] = useState(true);

  const columns = useMemo(() => {
    const seen = new Set<string>();
    for (const row of rows) Object.keys(row).forEach((key) => seen.add(key));
    // Keep the identifying columns first, then metrics alphabetically.
    const lead = ["rank", "design_id", "mutations"].filter((c) => seen.has(c));
    const rest = [...seen].filter((c) => !lead.includes(c)).sort();
    return [...lead, ...rest];
  }, [rows]);

  const sorted = useMemo(() => {
    if (!sortKey) return rows;
    return [...rows].sort((a, b) => {
      const x = a[sortKey];
      const y = b[sortKey];
      if (typeof x === "number" && typeof y === "number") {
        return descending ? y - x : x - y;
      }
      return descending
        ? String(y ?? "").localeCompare(String(x ?? ""))
        : String(x ?? "").localeCompare(String(y ?? ""));
    });
  }, [rows, sortKey, descending]);

  if (!rows.length) return <p className="muted">No rows.</p>;

  return (
    <div className="table-wrap">
      {data?.metric && (
        <p className="muted">
          Ranked by <strong>{data.metric}</strong> ({data.direction === "min" ? "lower" : "higher"}{" "}
          is better).
        </p>
      )}
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column}
                onClick={() => {
                  if (sortKey === column) setDescending(!descending);
                  else {
                    setSortKey(column);
                    setDescending(true);
                  }
                }}
                className={sortKey === column ? "sorted" : ""}
              >
                {column.replace(/_/g, " ")}
                {sortKey === column && (descending ? " ▾" : " ▴")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, index) => (
            <tr key={String(row.design_id ?? index)}>
              {columns.map((column) => (
                <td key={column}>{formatCell(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value.toPrecision(4);
  }
  return String(value);
}
