import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { resetPassword, ApiError } from "../api/client";
import { ThemeToggle } from "../components/ThemeToggle";
import { PasswordInput } from "../components/PasswordInput";
import { PasswordRulesList } from "../components/PasswordRulesList";

export function ResetPasswordPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const navigate = useNavigate();

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (password !== confirmPassword) {
      setError("Passwords don't match.");
      return;
    }

    setSubmitting(true);
    try {
      await resetPassword(token!, password);
      navigate("/login", { state: { passwordReset: true } });
    } catch (err) {
      if (err instanceof ApiError && err.message.includes("reset password bad token")) {
        setError("This reset link is invalid or has expired. Request a new one below.");
      } else {
        setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen">
      <div className="flex justify-end p-4">
        <ThemeToggle />
      </div>
      <div className="mx-auto max-w-sm mt-8">
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Choose a new password</h1>
        <p className="text-muted-foreground mb-8 text-sm">
          This link only works once and expires an hour after it was requested.
        </p>

        {!token ? (
          <div className="space-y-4">
            <p className="text-sm text-red-600">
              This link is missing its reset token. Request a new one below.
            </p>
            <Link
              to="/forgot-password"
              className="block text-center rounded-md bg-primary text-primary-foreground py-2 font-medium hover:opacity-90 transition"
            >
              Request a new link
            </Link>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1" htmlFor="password">
                New password
              </label>
              <PasswordInput
                id="password"
                required
                autoComplete="new-password"
                value={password}
                onChange={setPassword}
              />
              {password && <PasswordRulesList password={password} />}
            </div>

            <div>
              <label className="block text-sm font-medium mb-1" htmlFor="confirm-password">
                Confirm new password
              </label>
              <PasswordInput
                id="confirm-password"
                required
                autoComplete="new-password"
                value={confirmPassword}
                onChange={setConfirmPassword}
              />
            </div>

            {error && <p className="text-sm text-red-600">{error}</p>}

            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-md bg-primary text-primary-foreground py-2 font-medium hover:opacity-90 transition disabled:opacity-50"
            >
              {submitting ? "Resetting…" : "Reset password"}
            </button>
          </form>
        )}

        <p className="mt-8 text-center text-sm text-muted-foreground">
          <Link to="/login" className="text-ring hover:underline">
            Back to sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
