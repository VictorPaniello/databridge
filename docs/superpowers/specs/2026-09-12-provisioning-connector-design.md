# Provisioning connector: design

## Problem

tidybridge's pipeline currently ends at *notify*: clean a CSV/Excel upload, persist
it, fire a generic webhook saying "a new record arrived." What happens next - the
part where a real Forward Deployed Engineer would actually wire that data into the
client's own system - doesn't exist. The webhook only ever notifies; nothing in
this app ever calls out and *does* something with the cleaned data.

Research into what FDEs actually do (see sources below) converges on "build
connectors" and "wire up an API" as core, recurring work - and one specific,
well-known instance of that is **SCIM** (System for Cross-domain Identity
Management), the real industry-standard protocol identity providers (Okta, Entra
ID, Google Workspace) use to provision user accounts into target SaaS apps via
`POST /Users`. That's the concrete gap this spec closes: after a record is cleaned
and persisted, tidybridge should be able to actually provision it - create the
corresponding user on a configured downstream system - not just notify that it
exists.

Sources: [DataCamp - What Is a Forward Deployed Engineer?](https://www.datacamp.com/blog/what-is-forward-deployed-engineer),
[Forward Deployed Engineer - Wikipedia](https://en.wikipedia.org/wiki/Forward_Deployed_Engineer),
[Microsoft - What Is SCIM?](https://www.microsoft.com/en-us/security/business/security-101/what-is-scim),
[WorkOS - Scaling user provisioning with SCIM](https://workos.com/blog/scim-bulk-operations-filtering).

## Explicit scope decisions

These were confirmed during brainstorming, in order:

1. **Generic, configurable REST connector** - not a hardcoded integration
   against one named product (Auth0, HubSpot, etc.).
2. **New, separate mechanism alongside the existing webhook** - the webhook keeps
   doing exactly what it does today (notify any listener); this is a second,
   distinct post-ingest action, not a replacement and not built by overloading
   `WebhookJob`.
3. **Deployment-time YAML/env config, not DB/UI-configurable** - matches the
   existing `schema_path` (tidycsv schema) precedent exactly: one config per
   deployment, no per-user settings UI, no multi-tenant connector config.
4. **Fires automatically right after ingest** - same moment the webhook enqueues
   today, not a separate manual trigger step.
5. **SCIM-shaped by default** - the connector is mechanically generic (any target
   URL, any field mapping), but ships with the SCIM core-User mapping
   (`userName`, `name.givenName`/`familyName`, `emails[]`, `active`) as the
   realistic, recognizable default shape. This is explicitly **not** full SCIM
   protocol compliance (see "Out of scope" below).

A parallel idea - creating real tidybridge `User` accounts for the people listed
in an uploaded CSV and emailing them a signup invite - was raised and rejected
during brainstorming. Those rows are a client company's customers, who never
consented to a third-party tool emailing them; doing so is a real privacy/consent
problem (not just scope creep) and conflates two trust boundaries the app's data
model currently keeps separate (`User` = the engineer who owns records; a
`ClientRecord` row = data *about* someone else, with no login or agency in the
system). Not part of this spec.

## Data model

Two new tables, structurally mirroring `WebhookJob`/`WebhookDelivery` (the
proven, already-tested pattern - reused, not copied code):

### `ProvisioningJob`
One row per record needing provisioning - scheduling only, same shape as
`WebhookJob` plus one field neither `WebhookJob` nor a notify-only webhook ever
needed:

| column | type | notes |
|---|---|---|
| `id` | UUID | |
| `record_id` | UUID FK → `client_records`, `ondelete=cascade` | |
| `idempotency_key` | UUID | generated once at enqueue time, same convention as `WebhookJob` |
| `status` | str | `"pending"` \| `"done"` \| `"skipped_exists"` \| `"dead"` |
| `attempt_number` | int | default 1 |
| `available_at` | datetime | same backoff scheduling as `WebhookJob` |
| `remote_id` | str, nullable | the user ID the target system returns on success - needed for any future update/dedup, which is exactly why this doesn't fit `WebhookDelivery`'s shape |
| `created_at` | datetime | |

`status="skipped_exists"` is the one new state relative to webhooks: a real SCIM
server rejects a duplicate `userName` with `409 Conflict`, and that must be
treated as **terminal success-equivalent**, not a failure to retry - otherwise a
retry storm re-hits the conflict forever.

### `ProvisioningAttempt`
One row per actual HTTP attempt - the audit log, mirrors `WebhookDelivery`
exactly:

| column | type | notes |
|---|---|---|
| `id` | int, autoincrement | |
| `record_id` | UUID FK → `client_records`, `ondelete=CASCADE` | |
| `attempt_number` | int | |
| `status_code` | int, nullable | |
| `success` | bool | `True` for both `2xx` and `409` - "success" here means "resolved, no more attempts needed," matching the job-status semantics where `done` and `skipped_exists` both stop retrying |
| `error` | str, nullable | |
| `attempted_at` | datetime | |

## Enqueue flow

Same hook point as the webhook today: right after a record is persisted during
upload. A new `enqueue_provisioning(record)` does the `ProvisioningJob`
equivalent of `enqueue_delivery()`, gated by a `provisioning_url` setting being
set - `None`/unset disables it entirely, identical to `webhook_url`'s existing
disable pattern. The upload call site gains one more function call; no branching
logic needed.

## Worker flow

Extends the existing `webhook_worker.py` process (already running as its own
Railway service) rather than deploying a second service. `process_due_jobs()`
gains a second pass: claim due `ProvisioningJob` rows the same row-locked way it
claims `WebhookJob` rows, build the SCIM-shaped request body via the configured
field mapping, POST it, and interpret the response:

- `2xx` → `status="done"`, `remote_id` captured from the response body, successful
  `ProvisioningAttempt` written.
- `409 Conflict` → `status="skipped_exists"` (terminal, not a failure).
- Anything else (`5xx`, timeout, connection error) → same
  backoff/attempt-increment/eventual-`dead` path the webhook worker already has.
  The backoff calculation is extracted into a shared helper if it isn't already
  a standalone function, so both workers call the same tested logic rather than
  duplicating it.

Same `webhook_max_attempts`-equivalent setting controls when a job goes `dead`.

## API endpoints

Mirrors the existing webhook endpoints exactly - same auth
(`current_active_user`), same ownership check (`_get_owned_record`), same
routing-shadow care (registered before any path-param route that could swallow
it):

- **`GET /records/{id}/provisioning-status`** → `{status, attempt_number,
  available_at, remote_id}`. No `ProvisioningJob` row → `status:
  "not_configured"`, same convention as `webhook-status`.
- **`GET /records/{id}/provisioning`** → list of `ProvisioningAttempt` rows (the
  audit trail), same shape/purpose as `GET /records/{id}/webhooks`.
- **`POST /records/{id}/provisioning/replay`** → manually enqueue a fresh
  attempt. No special-casing for "already done/skipped_exists" - replaying an
  already-provisioned record just hits `409` again and comes back
  `skipped_exists`, which is naturally idempotent and harmless.

No new rate limit - the global 60/min default applies, matching the webhook
replay endpoint's own lack of a dedicated stricter limit.

## Frontend

A second section on `RecordDetailPage`, "Provisioning," directly mirroring the
existing "Webhook deliveries" section: a status badge (`not_configured` renders
nothing; `pending`/`skipped_exists`/`dead`/`done` each get their own badge), a
"Retry provisioning" button calling the replay endpoint, and - new relative to
the webhook section - showing `remote_id` once one exists (e.g. "Provisioned as
`usr_8f3a...` on the target system"). No new frontend components needed; reuses
the existing badge/button/table patterns already in `RecordDetailPage.tsx`.

## Config

One new settings block, same spirit as `schema_path`/`webhook_url` - a single
YAML file plus two settings (`provisioning_url`, `provisioning_api_key`, both
optional/`None`-disables). Loaded once at startup the same way `load_schema()`
loads `schema.yaml`.

```yaml
target_url: "${PROVISIONING_URL}/Users"
auth_header: "Bearer ${PROVISIONING_API_KEY}"
mapping:
  userName: email
  name.givenName: full_name  # split on first space - documented limitation
  name.familyName: full_name
  emails[0].value: email
  active: "true"
```

One file, no DB, no per-user config - consistent with the schema-mapping
precedent.

## Testing strategy

Matches this codebase's existing rigor (real Postgres via the `db`/`client`
fixtures, real HTTP assertions, not mocked ORM). New `test_provisioning_worker.py`
/ `test_provisioning.py`, mirroring `test_webhook_worker.py` / `test_webhooks.py`:

- Enqueue: `provisioning_url` unset → no job created; set → job created with
  expected defaults.
- Worker success: `2xx` → `done`, `remote_id` captured, successful attempt
  logged.
- Worker conflict: `409` → `skipped_exists` (terminal).
- Worker transient failure: `5xx`/timeout → attempt increments, backoff applied,
  failed attempt logged; exhausting max attempts → `dead`.
- Endpoints: `not_configured` with no job; correct shape once one exists; replay
  enqueues a fresh attempt; ownership enforced (404 on another engineer's
  record); auth required.
- Field-mapping unit test: YAML-driven mapping builds the correct SCIM-shaped
  JSON body from a `ClientRecord`, including the `full_name`-split-on-first-space
  limitation.

## Out of scope (explicitly, for this spec)

- **Full SCIM protocol compliance** - filtering, `PATCH`, the `/Bulk` endpoint,
  `/Schemas` discovery, groups. Only `POST /Users`-shaped creation.
- **Multi-connector / plugin framework** - exactly one configurable target per
  deployment, same as tidycsv's one `schema.yaml`.
- **DB- or UI-configurable connector settings** - stays a deployment-time
  YAML/env config.
- **CSV export columns for provisioning status** - a reasonable follow-up later,
  not part of this spec (mirrors how `webhook_status` export columns were their
  own separate piece of work).
- **OAuth2 or other complex auth flows for the target API** - a static bearer
  token from settings is the realistic default for most SCIM implementations
  anyway, not a corner cut.
- **Creating real tidybridge `User` accounts from CSV rows** - see "A parallel
  idea... was raised and rejected" above.
