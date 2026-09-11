import type {
  ClientRecord,
  CurrentUser,
  IngestionRun,
  IngestionRunsPage,
  IngestResult,
  RecordsPage,
  WebhookDelivery,
  WebhookJobStatus,
} from "./types";

const API_URL = import.meta.env.VITE_API_URL;
const TOKEN_KEY = "tidybridge_token";

// A plain module-level variable, not React state - the API client has no
// business knowing about React. AuthContext reads/writes it and is the
// single source of truth for components; localStorage only exists so a
// page reload doesn't force a fresh login.
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// fastapi-users' error bodies are either {"detail": "SOME_CODE"} or
// {"detail": {"code": "SOME_CODE", "reason": "human sentence"}}; plain
// FastAPI HTTPException bodies are {"detail": "human sentence"}. This
// covers all three rather than assuming one shape.
function extractErrorMessage(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") {
      return detail.replace(/_/g, " ").toLowerCase();
    }
    if (typeof detail === "object" && detail !== null && "reason" in detail) {
      const reason = (detail as { reason: unknown }).reason;
      if (typeof reason === "string") return reason;
    }
  }
  return fallback;
}

interface RequestOptions {
  method?: string;
  body?: BodyInit;
  headers?: Record<string, string>;
  auth?: boolean;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { ...options.headers };
  if (options.auth !== false) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_URL}${path}`, {
    method: options.method ?? "GET",
    body: options.body,
    headers,
    // Cross-origin (frontend and API are different origins), so fetch's
    // default credentials mode ("same-origin") would silently drop any
    // Set-Cookie response header instead of storing it. Only cookie in
    // play is the GitHub OAuth CSRF token /auth/github/authorize sets
    // (see auth.py) - regular auth doesn't use cookies at all, it's the
    // bearer JWT in the Authorization header - but without this, that
    // cookie never gets stored and the OAuth callback 400s with
    // OAUTH_INVALID_STATE. The backend's CORS config already sets
    // allow_credentials=True to allow this.
    credentials: "include",
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : null;

  if (!response.ok) {
    throw new ApiError(
      response.status,
      extractErrorMessage(payload, `Request failed with status ${response.status}`),
    );
  }

  return payload as T;
}

export interface RegisterInput {
  email: string;
  password: string;
  firstName: string;
  lastName: string;
  phone?: string;
}

export async function register(input: RegisterInput): Promise<void> {
  await request<void>("/auth/register", {
    method: "POST",
    auth: false,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: input.email,
      password: input.password,
      first_name: input.firstName,
      last_name: input.lastName,
      phone: input.phone || null,
    }),
  });
}

export async function login(email: string, password: string): Promise<string> {
  // fastapi-users' JWT login route is OAuth2PasswordRequestForm-shaped:
  // application/x-www-form-urlencoded with a "username" field (the email),
  // not JSON.
  const body = new URLSearchParams({ username: email, password });
  const { access_token } = await request<{ access_token: string; token_type: string }>(
    "/auth/jwt/login",
    {
      method: "POST",
      auth: false,
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    },
  );
  return access_token;
}

export async function logout(): Promise<void> {
  await request<void>("/auth/jwt/logout", { method: "POST" });
}

// Always resolves 202 regardless of whether the email is registered -
// anti-enumeration by design, see auth.py. The one exception is
// oauth_only: true, which does confirm the account exists (and is
// GitHub-only) - a deliberate, narrower tradeoff than a fully generic
// response, made so the page can tell someone "sign in with GitHub"
// instead of leaving them waiting on an email that will never arrive
// (see auth.py's UserManager.forgot_password()/forgot_password_handler
// for the backend side of why no email is sent in that case).
export async function forgotPassword(email: string): Promise<{ oauthOnly: boolean }> {
  const { oauth_only } = await request<{ oauth_only: boolean }>("/auth/forgot-password", {
    method: "POST",
    auth: false,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email }),
  });
  return { oauthOnly: oauth_only };
}

// Throws ApiError with a readable message on an invalid/expired token
// (RESET_PASSWORD_BAD_TOKEN) or a password that fails the same strength
// rules registration enforces (RESET_PASSWORD_INVALID_PASSWORD, whose
// `reason` extractErrorMessage already surfaces).
export async function resetPassword(token: string, password: string): Promise<void> {
  await request<void>("/auth/reset-password", {
    method: "POST",
    auth: false,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, password }),
  });
}

export async function getCurrentUser(): Promise<CurrentUser> {
  return request<CurrentUser>("/users/me");
}

export interface ProfileUpdate {
  firstName: string;
  lastName: string;
  phone?: string;
  // fastapi-users' BaseUserUpdate (which auth.py's UserUpdate extends)
  // already carries an optional `password` field, validated through the
  // same UserManager.validate_password override the registration policy
  // uses - no backend change needed to support changing it here.
  password?: string;
}

// PATCH /users/me is fastapi-users' generic update route - accepts a
// partial UserUpdate body (see auth.py), which is why first_name/
// last_name/phone can be set here the same way GitHub OAuth signups
// complete their profile as email+password ones set it at registration.
export async function updateProfile(input: ProfileUpdate): Promise<CurrentUser> {
  return request<CurrentUser>("/users/me", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      first_name: input.firstName,
      last_name: input.lastName,
      phone: input.phone || null,
      ...(input.password ? { password: input.password } : {}),
    }),
  });
}

// Permanently erases the caller's own account and everything tied to it
// (every client record, ingestion run, and webhook delivery they own,
// plus any linked GitHub OAuth account) - see main.py's
// delete_own_account docstring. Real, irreversible deletion, not a
// deactivation - AuthContext.deleteAccount() clears the local token
// right after this resolves, since the token would 401 on its own very
// next use anyway (the user id it names no longer exists).
export async function deleteAccount(): Promise<void> {
  await request<void>("/users/me", { method: "DELETE" });
}

// Not fetched: this used to be `await fetch("/auth/github/authorize")`
// then `window.location.href = <the JSON body's authorization_url>`, but
// that route sets a CSRF cookie the browser needs to send back on the
// callback - setting it via a cross-origin fetch (frontend and API are
// different origins) gets silently dropped by browsers that block
// third-party cookies by default. A plain top-level navigation to this
// URL is itself now a redirect straight to GitHub (see the backend's
// github_authorize_redirect), so the cookie gets set first-party instead.
export function githubAuthorizeUrl(): string {
  return `${API_URL}/auth/github/authorize`;
}

export async function uploadFile(file: File): Promise<IngestResult> {
  const formData = new FormData();
  formData.append("file", file);
  return request<IngestResult>("/records/upload", { method: "POST", body: formData });
}

// Same walk-every-page approach as listRecords() below, for the same
// reason - GET /ingestion-runs is server-paginated (see main.py), and
// this page just wants the full history, not a manual "load more" flow.
export async function listIngestionRuns(): Promise<IngestionRun[]> {
  const all: IngestionRun[] = [];
  let offset = 0;

  while (true) {
    const page = await request<IngestionRunsPage>(
      `/ingestion-runs?limit=500&offset=${offset}`,
    );
    all.push(...page.items);
    offset += page.items.length;
    if (page.items.length === 0 || offset >= page.total) break;
  }

  return all;
}

// GET /records is now server-paginated (a hard cap of 500 rows per
// request - see main.py) rather than returning every matching row in
// one unbounded response. RecordsPage.tsx still does its filter/search/
// sort/stats over the *full* set in memory (fine at this project's
// scale - one engineer's own records), so this walks every page and
// concatenates them, rather than pushing pagination up into the UI. The
// win isn't fewer records fetched - it's that no single request (or the
// one query behind it) is ever unbounded, however large the account
// grows; a future "load more" UI could reuse this same paginated
// endpoint without any backend change.
const MAX_PAGE_SIZE = 500;

export async function listRecords(
  hasIssues?: boolean,
  ingestionRunId?: string,
): Promise<ClientRecord[]> {
  let filter = hasIssues === undefined ? "" : `&has_issues=${hasIssues}`;
  if (ingestionRunId) filter += `&ingestion_run_id=${ingestionRunId}`;
  const all: ClientRecord[] = [];
  let offset = 0;

  while (true) {
    const page = await request<RecordsPage>(
      `/records?limit=${MAX_PAGE_SIZE}&offset=${offset}${filter}`,
    );
    all.push(...page.items);
    offset += page.items.length;
    if (page.items.length === 0 || offset >= page.total) break;
  }

  return all;
}

// Not routed through request() - that helper always expects JSON back;
// this is a raw CSV file the browser needs to save, not parse. Reuses
// its exact auth-header logic anyway: a plain <a href> link can't carry
// a bearer token (only cookies travel with a plain navigation, and this
// API doesn't use those for auth), so the download has to go through a
// real fetch() first.
export async function exportRecords(ingestionRunId?: string): Promise<void> {
  const filter = ingestionRunId ? `?ingestion_run_id=${ingestionRunId}` : "";
  const token = getToken();
  const response = await fetch(`${API_URL}/records/export${filter}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    credentials: "include",
  });
  if (!response.ok) {
    throw new ApiError(response.status, "Couldn't export records.");
  }

  const blob = await response.blob();
  // Reuses the filename the backend already generated (its
  // Content-Disposition header - see main.py's export_records() and the
  // CORS expose_headers config that makes this readable cross-origin)
  // rather than making up a second one here.
  const disposition = response.headers.get("content-disposition") ?? "";
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? "tidybridge-records.csv";

  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

export async function getRecord(id: string): Promise<ClientRecord> {
  return request<ClientRecord>(`/records/${id}`);
}

export async function getRecordWebhooks(id: string): Promise<WebhookDelivery[]> {
  return request<WebhookDelivery[]>(`/records/${id}/webhooks`);
}

export async function getRecordWebhookStatus(id: string): Promise<WebhookJobStatus> {
  return request<WebhookJobStatus>(`/records/${id}/webhook-status`);
}

// Manually re-sends the notification for one record, on demand - a real,
// separate action from the automatic retries the backend already does
// (see webhooks.py), not another one of them. Throws ApiError(400) if no
// WEBHOOK_URL is configured server-side - there's nothing to replay to.
export async function replayWebhook(id: string): Promise<WebhookDelivery> {
  return request<WebhookDelivery>(`/records/${id}/webhooks/replay`, { method: "POST" });
}

export async function deleteRecord(id: string): Promise<void> {
  await request<void>(`/records/${id}`, { method: "DELETE" });
}
