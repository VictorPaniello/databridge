// Mirrors tidybridge/schemas.py and auth.py's UserRead - kept hand-in-sync
// with the backend since this is a small, single-frontend project, not
// generated from an OpenAPI spec.

export interface ClientRecord {
  id: string;
  // Which upload created this record - null for anything ingested before
  // ingestion_run_id existed. See IngestionRun below.
  ingestion_run_id: string | null;
  source_file: string;
  full_name: string | null;
  email: string | null;
  signup_date: string | null;
  amount: string | null;
  phone: string | null;
  has_issues: boolean;
  issues: { field: string; issue: string }[] | null;
  created_at: string;
}

export interface WebhookDelivery {
  id: number;
  record_id: string;
  url: string;
  status_code: number | null;
  success: boolean;
  error: string | null;
  // 1 for the first try, 2+ for each retry after it (up to the
  // backend's webhook_max_attempts) - one row per attempt, not one row
  // overwritten in place, so a record can have several of these.
  attempt_number: number;
  attempted_at: string;
}

export interface IngestResult {
  ingestion_run_id: string;
  rows_total: number;
  rows_clean: number;
  rows_flagged: number;
  rows_dropped_duplicates: number;
  // Rows that matched an email already ingested in a *previous* upload -
  // re-uploading the same list is a no-op, not an error, but those rows
  // still need to be accounted for: rows_total always equals rows_clean +
  // rows_flagged + rows_dropped_duplicates + this field.
  rows_skipped_existing: number;
  records: ClientRecord[];
}

// The persisted counterpart of IngestResult (GET /ingestion-runs and
// /ingestion-runs/{id}) - what IngestResult never carried: when it
// happened and which file it was, so a run is still findable after the
// upload response that first reported it is long gone.
export interface IngestionRun {
  id: string;
  source_file: string;
  rows_total: number;
  rows_clean: number;
  rows_flagged: number;
  rows_dropped_duplicates: number;
  rows_skipped_existing: number;
  created_at: string;
}

export interface IngestionRunsPage {
  items: IngestionRun[];
  total: number;
  limit: number;
  offset: number;
}

// GET /records used to return a bare ClientRecord[] - every matching row
// in one response. It's now a bounded page (server-enforced limit<=500,
// see main.py) plus total, so a caller can tell how many more rows exist
// beyond the ones it got back.
export interface RecordsPage {
  items: ClientRecord[];
  total: number;
  limit: number;
  offset: number;
}

// The automatic (post-ingest) delivery pipeline's current state for one
// record - see the backend's WebhookJobStatusOut docstring for what each
// status means. Doesn't reflect manual replays.
export interface WebhookJobStatus {
  status: "pending" | "done" | "dead" | "not_configured";
  attempt_number: number | null;
  available_at: string | null;
}

export interface CurrentUser {
  id: string;
  email: string;
  is_active: boolean;
  is_superuser: boolean;
  is_verified: boolean;
  // null for any user who never went through /auth/register - notably
  // every GitHub OAuth signup (see the backend's UserRead docstring).
  first_name: string | null;
  last_name: string | null;
  phone: string | null;
}
