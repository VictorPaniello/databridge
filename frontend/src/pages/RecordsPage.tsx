import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../api/client";
import { ApiError } from "../api/client";
import type { ClientRecord, IngestResult } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { greeting } from "../lib/greeting";

type Filter = "all" | "clean" | "flagged";

export function RecordsPage() {
  const { user } = useAuth();
  const [records, setRecords] = useState<ClientRecord[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<IngestResult | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const hasIssues = filter === "all" ? undefined : filter === "flagged";

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRecords(await api.listRecords(hasIssues));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't load records.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // lets the same filename be re-selected later
    if (!file) return;

    setUploading(true);
    setUploadError(null);
    setLastResult(null);
    try {
      const result = await api.uploadFile(file);
      setLastResult(result);
      await load();
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  async function handleDelete(id: string) {
    if (!confirm("Delete this record permanently? This cannot be undone.")) return;
    try {
      await api.deleteRecord(id);
      setRecords((prev) => prev.filter((r) => r.id !== id));
    } catch (err) {
      alert(err instanceof ApiError ? err.message : "Couldn't delete this record.");
    }
  }

  return (
    <div>
      {user && <p className="text-sm text-muted-foreground mb-1">{greeting(user)}</p>}
      <div className="flex flex-wrap items-center justify-between gap-4 mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Client records</h1>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={handleFileChange}
            className="hidden"
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="rounded-md bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {uploading ? "Uploading…" : "Upload CSV / Excel"}
          </button>
        </div>
      </div>

      {uploadError && (
        <div className="mb-4 rounded-md border border-red-300 bg-red-50 dark:bg-red-950/30 dark:border-red-900 px-4 py-3 text-sm text-red-700 dark:text-red-300">
          {uploadError}
        </div>
      )}

      {lastResult && (
        <div className="mb-6 rounded-md border border-border px-4 py-3 text-sm">
          <span className="font-medium">{lastResult.rows_total}</span> rows processed —{" "}
          <span className="text-primary">{lastResult.rows_clean} clean</span>,{" "}
          <span className="text-amber-600 dark:text-amber-400">
            {lastResult.rows_flagged} flagged
          </span>
          , {lastResult.rows_dropped_duplicates} duplicate(s) skipped.
        </div>
      )}

      <div className="flex gap-2 mb-4 text-sm">
        {(["all", "clean", "flagged"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`rounded-full px-3 py-1 border transition ${
              filter === f
                ? "bg-primary text-primary-foreground border-primary"
                : "border-border hover:bg-secondary"
            }`}
          >
            {f === "all" ? "All" : f === "clean" ? "Clean" : "Flagged"}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="text-muted-foreground text-sm">Loading…</p>
      ) : error ? (
        <p className="text-red-500 text-sm">{error}</p>
      ) : records.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No records yet. Upload a CSV or Excel file to get started.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-secondary text-left text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Email</th>
                <th className="px-4 py-2 font-medium">Signup date</th>
                <th className="px-4 py-2 font-medium">Amount</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {records.map((r) => (
                <tr key={r.id} className="border-t border-border hover:bg-secondary/50">
                  <td className="px-4 py-2">
                    <Link to={`/records/${r.id}`} className="hover:underline">
                      {r.full_name ?? "—"}
                    </Link>
                  </td>
                  <td className="px-4 py-2 text-muted-foreground">{r.email ?? "—"}</td>
                  <td className="px-4 py-2 text-muted-foreground">{r.signup_date ?? "—"}</td>
                  <td className="px-4 py-2 text-muted-foreground">{r.amount ?? "—"}</td>
                  <td className="px-4 py-2">
                    {r.has_issues ? (
                      <span className="rounded-full bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400 px-2 py-0.5 text-xs">
                        Flagged
                      </span>
                    ) : (
                      <span className="rounded-full bg-accent text-accent-foreground px-2 py-0.5 text-xs">
                        Clean
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <button
                      onClick={() => handleDelete(r.id)}
                      className="text-muted-foreground hover:text-red-500 transition text-xs"
                    >
                      Delete
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
