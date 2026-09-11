import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import * as api from "../api/client";
import { ApiError } from "../api/client";
import type { ClientRecord, IngestResult } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { greeting } from "../lib/greeting";
import { ConfirmDialog } from "../components/ConfirmDialog";

type Filter = "all" | "clean" | "flagged";

// full_name/email/signup_date sort alphabetically (signup_date is stored
// as an ISO-ish string, so alphabetical order already matches chronological
// order); amount sorts numerically; has_issues (Status) sorts by its
// Clean/Flagged label, alphabetically - Clean before Flagged ascending,
// same string-comparator behavior as the other non-numeric columns.
type SortKey = "full_name" | "email" | "signup_date" | "amount" | "has_issues";
type SortDirection = "asc" | "desc";
interface SortState {
  key: SortKey;
  direction: SortDirection;
}

const NUMERIC_SORT_KEYS: SortKey[] = ["amount"];

function sortValue(record: ClientRecord, key: SortKey): string | number | null {
  if (key === "has_issues") return record.has_issues ? "Flagged" : "Clean";
  return record[key];
}

function compareRecords(a: ClientRecord, b: ClientRecord, sort: SortState): number {
  const av = sortValue(a, sort.key);
  const bv = sortValue(b, sort.key);
  // Nulls always sort last regardless of direction - an unfilled field
  // isn't meaningfully "before" or "after" real data.
  if (av == null && bv == null) return 0;
  if (av == null) return 1;
  if (bv == null) return -1;

  const cmp = NUMERIC_SORT_KEYS.includes(sort.key)
    ? parseFloat(av as string) - parseFloat(bv as string)
    : (av as string).localeCompare(bv as string);

  return sort.direction === "asc" ? cmp : -cmp;
}

function SortIcon({ direction }: { direction: SortDirection | null }) {
  return (
    <svg width="10" height="12" viewBox="0 0 10 12" fill="none" aria-hidden="true">
      <path
        d="M5 0L9 4.5H1L5 0Z"
        fill="currentColor"
        opacity={direction === "asc" ? 1 : 0.3}
      />
      <path
        d="M5 12L1 7.5H9L5 12Z"
        fill="currentColor"
        opacity={direction === "desc" ? 1 : 0.3}
      />
    </svg>
  );
}

function SortableHeader({
  label,
  sortKey,
  sort,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState | null;
  onSort: (key: SortKey) => void;
}) {
  const active = sort?.key === sortKey;
  return (
    <th className="px-4 py-2 font-medium">
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className="flex items-center gap-1.5 hover:text-foreground transition"
      >
        {label}
        <SortIcon direction={active ? sort.direction : null} />
      </button>
    </th>
  );
}

function StatCard({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: string;
  valueClassName?: string;
}) {
  return (
    <div className="rounded-md border border-border bg-card px-4 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`text-2xl font-semibold tracking-tight ${valueClassName ?? ""}`}>
        {value}
      </div>
    </div>
  );
}

export function RecordsPage() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const ingestionRunId = searchParams.get("ingestion_run_id");
  const [records, setRecords] = useState<ClientRecord[]>([]);
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [lastResult, setLastResult] = useState<IngestResult | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Fetched once, unfiltered (beyond an optional ?ingestion_run_id= from
  // the URL - see the "Upload history" page's "View records" links) -
  // filter/search/stats are all derived from this in-memory list below,
  // rather than a fresh request per filter change. Fine at this
  // project's scale (one engineer's own records), and it's what makes
  // the stats panel possible without a second endpoint just to count
  // things the client already has.
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setRecords(await api.listRecords(undefined, ingestionRunId ?? undefined));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't load records.");
    } finally {
      setLoading(false);
    }
  }, [ingestionRunId]);

  useEffect(() => {
    load();
  }, [load]);

  const stats = useMemo(() => {
    const total = records.length;
    const flagged = records.filter((r) => r.has_issues).length;
    return { total, clean: total - flagged, flagged };
  }, [records]);

  const filteredRecords = useMemo(() => {
    const query = search.trim().toLowerCase();
    return records.filter((r) => {
      if (filter === "clean" && r.has_issues) return false;
      if (filter === "flagged" && !r.has_issues) return false;
      if (query) {
        const matchesName = r.full_name?.toLowerCase().includes(query);
        const matchesEmail = r.email?.toLowerCase().includes(query);
        if (!matchesName && !matchesEmail) return false;
      }
      return true;
    });
  }, [records, filter, search]);

  const sortedRecords = useMemo(() => {
    if (!sort) return filteredRecords;
    return [...filteredRecords].sort((a, b) => compareRecords(a, b, sort));
  }, [filteredRecords, sort]);

  function handleSort(key: SortKey) {
    setSort((prev) => {
      if (prev?.key === key) {
        return { key, direction: prev.direction === "asc" ? "desc" : "asc" };
      }
      return { key, direction: "asc" };
    });
  }

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

  async function confirmDelete() {
    if (!pendingDeleteId) return;
    const id = pendingDeleteId;
    setPendingDeleteId(null);
    try {
      await api.deleteRecord(id);
      setRecords((prev) => prev.filter((r) => r.id !== id));
    } catch (err) {
      alert(err instanceof ApiError ? err.message : "Couldn't delete this record.");
    }
  }

  return (
    <div>
      {user && <p className="text-lg text-muted-foreground mb-1">{greeting(user)}</p>}
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

      {ingestionRunId && (
        <div className="mb-4 flex items-center justify-between rounded-md border border-border bg-secondary/50 px-4 py-2 text-sm">
          <span>
            Showing only records from{" "}
            <Link to="/uploads" className="text-ring hover:underline">
              one upload
            </Link>
          </span>
          <button
            type="button"
            onClick={() => setSearchParams({})}
            className="text-xs text-muted-foreground hover:underline"
          >
            Clear filter
          </button>
        </div>
      )}

      {uploadError && (
        <div className="mb-4 rounded-md border border-red-300 bg-red-50 dark:bg-red-950/30 dark:border-red-900 px-4 py-3 text-sm text-red-700 dark:text-red-300">
          {uploadError}
        </div>
      )}

      {lastResult && (
        <div className="mb-6 flex items-start justify-between gap-3 rounded-md border border-border px-4 py-3 text-sm">
          <p>
            <span className="font-medium">{lastResult.rows_total}</span> rows processed —{" "}
            <span className="text-primary">{lastResult.rows_clean} clean</span>,{" "}
            <span className="text-amber-700 dark:text-amber-400">
              {lastResult.rows_flagged} flagged
            </span>
            , {lastResult.rows_dropped_duplicates} duplicate(s) skipped
            {lastResult.rows_skipped_existing > 0 &&
              `, ${lastResult.rows_skipped_existing} already ingested`}
            .{" "}
            <Link to={`/uploads`} className="text-ring hover:underline">
              View full history
            </Link>
          </p>
          <button
            type="button"
            onClick={() => setLastResult(null)}
            aria-label="Dismiss"
            className="shrink-0 text-muted-foreground hover:text-foreground transition"
          >
            ✕
          </button>
        </div>
      )}

      {!loading && !error && records.length > 0 && (
        <div className="grid grid-cols-3 gap-4 mb-6">
          <StatCard label="Total records" value={String(stats.total)} />
          <StatCard label="Clean" value={String(stats.clean)} valueClassName="text-primary" />
          <StatCard
            label="Flagged"
            value={
              stats.total === 0
                ? "0"
                : `${stats.flagged} (${Math.round((stats.flagged / stats.total) * 100)}%)`
            }
            valueClassName="text-amber-700 dark:text-amber-400"
          />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="flex gap-2 text-sm">
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
        <input
          type="search"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by name or email…"
          className="ml-auto w-full sm:w-64 rounded-md border border-input bg-transparent px-3 py-1.5 text-sm outline-none focus:ring-2 focus:ring-ring"
        />
      </div>

      {loading ? (
        <p className="text-muted-foreground text-sm">Loading…</p>
      ) : error ? (
        <p className="text-red-600 text-sm">{error}</p>
      ) : records.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          No records yet. Upload a CSV or Excel file to get started.
        </p>
      ) : sortedRecords.length === 0 ? (
        <p className="text-muted-foreground text-sm">No records match this filter/search.</p>
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-secondary text-left text-muted-foreground">
              <tr>
                <SortableHeader label="Name" sortKey="full_name" sort={sort} onSort={handleSort} />
                <SortableHeader label="Email" sortKey="email" sort={sort} onSort={handleSort} />
                <SortableHeader
                  label="Signup date"
                  sortKey="signup_date"
                  sort={sort}
                  onSort={handleSort}
                />
                <SortableHeader label="Amount" sortKey="amount" sort={sort} onSort={handleSort} />
                <SortableHeader
                  label="Status"
                  sortKey="has_issues"
                  sort={sort}
                  onSort={handleSort}
                />
                <th className="px-4 py-2 font-medium" />
              </tr>
            </thead>
            <tbody>
              {sortedRecords.map((r) => (
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
                      onClick={() => setPendingDeleteId(r.id)}
                      className="rounded-md border border-red-300 dark:border-red-900 bg-card text-red-600 dark:text-red-400 px-2.5 py-1 text-xs shadow-sm hover:shadow transition"
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

      <ConfirmDialog
        open={pendingDeleteId !== null}
        title="Delete this record?"
        message="This permanently deletes the record and its webhook delivery history. This cannot be undone."
        onConfirm={confirmDelete}
        onCancel={() => setPendingDeleteId(null)}
      />
    </div>
  );
}
