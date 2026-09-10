// Mirrors databridge/schemas.py and auth.py's UserRead - kept hand-in-sync
// with the backend since this is a small, single-frontend project, not
// generated from an OpenAPI spec.

export interface ClientRecord {
  id: string;
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
  attempted_at: string;
}

export interface IngestResult {
  rows_total: number;
  rows_clean: number;
  rows_flagged: number;
  rows_dropped_duplicates: number;
  records: ClientRecord[];
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
