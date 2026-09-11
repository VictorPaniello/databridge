import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import * as api from "../api/client";
import { ApiError } from "../api/client";
import type { ClientRecord, WebhookDelivery, WebhookJobStatus } from "../api/types";
import { ConfirmDialog } from "../components/ConfirmDialog";

export function RecordDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [record, setRecord] = useState<ClientRecord | null>(null);
  const [webhooks, setWebhooks] = useState<WebhookDelivery[]>([]);
  const [webhookStatus, setWebhookStatus] = useState<WebhookJobStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [replaying, setReplaying] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    setError(null);
    Promise.all([api.getRecord(id), api.getRecordWebhooks(id), api.getRecordWebhookStatus(id)])
      .then(([recordResult, webhooksResult, statusResult]) => {
        setRecord(recordResult);
        setWebhooks(webhooksResult);
        setWebhookStatus(statusResult);
      })
      .catch((err) => {
        setError(
          err instanceof ApiError && err.status === 404
            ? "Record not found."
            : "Couldn't load this record.",
        );
      })
      .finally(() => setLoading(false));
  }, [id]);

  async function handleDelete() {
    if (!id) return;
    setConfirmingDelete(false);
    try {
      await api.deleteRecord(id);
      navigate("/");
    } catch {
      alert("Couldn't delete this record.");
    }
  }

  async function handleReplay() {
    if (!id) return;
    setReplaying(true);
    setReplayError(null);
    try {
      await api.replayWebhook(id);
      setWebhooks(await api.getRecordWebhooks(id)); // refresh to show the new attempt(s)
    } catch (err) {
      setReplayError(err instanceof ApiError ? err.message : "Couldn't resend the webhook.");
    } finally {
      setReplaying(false);
    }
  }

  if (loading) return <p className="text-muted-foreground text-sm">Loading…</p>;
  if (error) return <p className="text-red-600 text-sm">{error}</p>;
  if (!record) return null;

  return (
    <div>
      <Link to="/" className="text-sm text-ring hover:underline">
        ← Back to records
      </Link>

      <div className="mt-4 flex items-start justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">
          {record.full_name ?? "Unnamed record"}
        </h1>
        <button
          onClick={() => setConfirmingDelete(true)}
          className="rounded-md border border-red-300 dark:border-red-900 bg-card text-red-600 dark:text-red-400 px-3 py-1.5 text-sm shadow-sm hover:shadow hover:bg-red-50 dark:hover:bg-red-950/30 transition"
        >
          Delete
        </button>
      </div>

      <dl className="mt-6 grid grid-cols-2 sm:grid-cols-3 gap-4 text-sm">
        <Field label="Email" value={record.email} />
        <Field label="Phone" value={record.phone} />
        <Field label="Signup date" value={record.signup_date} />
        <Field label="Amount" value={record.amount} />
        <Field label="Source file" value={record.source_file} />
        <Field label="Ingested" value={new Date(record.created_at).toLocaleString()} />
      </dl>

      {record.has_issues && record.issues && (
        <div className="mt-8">
          <h2 className="text-sm font-semibold mb-2">Validation issues</h2>
          <ul className="rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-900 divide-y divide-amber-200 dark:divide-amber-900 text-sm">
            {record.issues.map((issue, i) => (
              <li key={i} className="px-4 py-2">
                <span className="font-medium">{issue.field}</span>: {issue.issue}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-8">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold">Webhook deliveries</h2>
            <WebhookStatusBadge status={webhookStatus} />
          </div>
          <button
            type="button"
            onClick={handleReplay}
            disabled={replaying}
            className="rounded-md border border-border px-3 py-1 text-xs hover:bg-secondary transition disabled:opacity-50"
          >
            {replaying ? "Resending…" : "Resend webhook"}
          </button>
        </div>
        {replayError && (
          <p className="mb-2 text-sm text-red-600">{replayError}</p>
        )}
        {webhooks.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No webhook was configured, or none has been attempted for this record.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-sm">
              <thead className="bg-secondary text-left text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 font-medium">Attempt</th>
                  <th className="px-4 py-2 font-medium">Attempted</th>
                  <th className="px-4 py-2 font-medium">URL</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Result</th>
                </tr>
              </thead>
              <tbody>
                {webhooks.map((w) => (
                  <tr key={w.id} className="border-t border-border">
                    <td className="px-4 py-2 text-muted-foreground">#{w.attempt_number}</td>
                    <td className="px-4 py-2 text-muted-foreground">
                      {new Date(w.attempted_at).toLocaleString()}
                    </td>
                    <td className="px-4 py-2 text-muted-foreground truncate max-w-xs">{w.url}</td>
                    <td className="px-4 py-2">{w.status_code ?? "—"}</td>
                    <td className="px-4 py-2">
                      {w.success ? (
                        <span className="text-primary">Delivered</span>
                      ) : (
                        <span className="text-red-600" title={w.error ?? undefined}>
                          Failed
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={confirmingDelete}
        title="Delete this record?"
        message="This permanently deletes the record and its webhook delivery history. This cannot be undone."
        onConfirm={handleDelete}
        onCancel={() => setConfirmingDelete(false)}
      />
    </div>
  );
}

function Field({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium">{value ?? "—"}</dd>
    </div>
  );
}

// The automatic post-ingest delivery pipeline's state, next to the
// "Webhook deliveries" heading - distinct from the per-attempt table
// below it, and from a manual replay (which this never reflects, see
// WebhookJobStatus's docstring in api/types.ts).
function WebhookStatusBadge({ status }: { status: WebhookJobStatus | null }) {
  if (!status || status.status === "not_configured") return null;

  if (status.status === "dead") {
    return (
      <span className="rounded-full bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400 px-2 py-0.5 text-xs">
        Delivery failed permanently
      </span>
    );
  }
  if (status.status === "done") {
    return (
      <span className="rounded-full bg-accent text-accent-foreground px-2 py-0.5 text-xs">
        Delivered
      </span>
    );
  }
  // "pending"
  return (
    <span className="rounded-full bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400 px-2 py-0.5 text-xs">
      {status.attempt_number && status.attempt_number > 1
        ? `Retrying (attempt ${status.attempt_number})`
        : "Delivery pending"}
    </span>
  );
}
