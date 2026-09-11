import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../api/client";
import { ApiError } from "../api/client";
import type { IngestionRun } from "../api/types";

export function IngestionRunsPage() {
  const [runs, setRuns] = useState<IngestionRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listIngestionRuns()
      .then(setRuns)
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "Couldn't load upload history.");
      })
      .finally(() => setLoading(false));
  }, []);

  async function handleExport(runId: string) {
    try {
      await api.exportRecords(runId);
    } catch {
      alert("Couldn't export this upload's records.");
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight mb-1">Upload history</h1>
      <p className="text-sm text-muted-foreground mb-6">
        Every file you've uploaded, and exactly what happened to each row in it - not just
        what the upload screen showed you at the time.
      </p>

      {loading ? (
        <p className="text-muted-foreground text-sm">Loading…</p>
      ) : error ? (
        <p className="text-red-600 text-sm">{error}</p>
      ) : runs.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No uploads yet. <Link to="/" className="text-ring hover:underline">Upload a file</Link>{" "}
          to get started.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-secondary text-left text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">File</th>
                <th className="px-4 py-2 font-medium">Uploaded</th>
                <th className="px-4 py-2 font-medium text-right">Total rows</th>
                <th className="px-4 py-2 font-medium text-right">Clean</th>
                <th className="px-4 py-2 font-medium text-right">Flagged</th>
                <th className="px-4 py-2 font-medium text-right">Duplicates</th>
                <th className="px-4 py-2 font-medium text-right">Already ingested</th>
                <th className="px-4 py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="border-t border-border">
                  <td className="px-4 py-2 font-medium">{run.source_file}</td>
                  <td className="px-4 py-2 text-muted-foreground">
                    {new Date(run.created_at).toLocaleString()}
                  </td>
                  <td className="px-4 py-2 text-right">{run.rows_total}</td>
                  <td className="px-4 py-2 text-right text-primary">{run.rows_clean}</td>
                  <td className="px-4 py-2 text-right text-amber-700 dark:text-amber-400">
                    {run.rows_flagged}
                  </td>
                  <td className="px-4 py-2 text-right text-muted-foreground">
                    {run.rows_dropped_duplicates}
                  </td>
                  <td className="px-4 py-2 text-right text-muted-foreground">
                    {run.rows_skipped_existing}
                  </td>
                  <td className="px-4 py-2 text-right whitespace-nowrap">
                    <Link
                      to={`/?ingestion_run_id=${run.id}`}
                      className="text-xs text-ring hover:underline"
                    >
                      View records
                    </Link>
                    {" · "}
                    <button
                      type="button"
                      onClick={() => handleExport(run.id)}
                      className="text-xs text-ring hover:underline"
                    >
                      Export
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
