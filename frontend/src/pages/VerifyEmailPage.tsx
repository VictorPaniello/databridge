import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { verifyEmail, ApiError } from "../api/client";
import { ThemeToggle } from "../components/ThemeToggle";

type Status = "verifying" | "success" | "already-verified" | "invalid" | "error";

// Public route - the link clicked from the actual email, so it has to
// work whether or not this browser happens to still hold a signed-in
// session from registration (a different device, a different browser, or
// just a token from days-old browser state are all real cases).
export function VerifyEmailPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const { user, refreshUser } = useAuth();
  const [status, setStatus] = useState<Status>(token ? "verifying" : "invalid");
  // React 18 StrictMode double-invokes effects in dev - without this
  // guard that would fire this token at the API twice. A per-closure
  // "cancelled" flag (the usual fix for a plain unmount-during-flight
  // race) doesn't compose with a ref guard like this one: StrictMode's
  // interim cleanup call would flip *this* invocation's own cancelled
  // flag before its promise ever resolves, since the guard stops the
  // second invocation from ever starting a fresh one - silently
  // discarding the real result. A single ref carrying the outcome
  // avoids that: whichever invocation's promise resolves first writes
  // it, and setStatus applies it unconditionally - safe even if called
  // after unmount (a harmless dev-only warning, not a real bug), which
  // is the actual scenario this needs to tolerate, not double-submission.
  const requestedToken = useRef<string | null>(null);

  useEffect(() => {
    if (!token || requestedToken.current === token) return;
    requestedToken.current = token;

    verifyEmail(token)
      .then(async () => {
        // Updates the signed-in user's is_verified in place, if this
        // browser has one - so ProtectedRoute stops redirecting to
        // /verify-email-pending the moment they navigate on, without
        // waiting for a reload. A no-op (early return) if nobody's
        // signed in here.
        await refreshUser();
        setStatus("success");
      })
      .catch((err) => {
        if (err instanceof ApiError && err.message.includes("already verified")) {
          setStatus("already-verified");
        } else if (err instanceof ApiError && err.message.includes("bad token")) {
          setStatus("invalid");
        } else {
          setStatus("error");
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  return (
    <div className="min-h-screen">
      <div className="flex justify-end p-4">
        <ThemeToggle />
      </div>
      <div className="mx-auto max-w-sm mt-8">
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Email verification</h1>

        {status === "verifying" && <p className="text-sm text-muted-foreground">Verifying…</p>}

        {(status === "success" || status === "already-verified") && (
          <div className="space-y-4">
            <p className="text-sm text-primary">
              {status === "success" ? "Email verified." : "This email was already verified."}
            </p>
            <Link
              to={user ? "/" : "/login"}
              className="block text-center rounded-md bg-primary text-primary-foreground py-2 font-medium hover:opacity-90 transition"
            >
              {user ? "Continue to your records" : "Sign in"}
            </Link>
          </div>
        )}

        {status === "invalid" && (
          <div className="space-y-4">
            <p className="text-sm text-red-500">
              This verification link is invalid or has expired.
            </p>
            <Link
              to={user ? "/verify-email-pending" : "/login"}
              className="block text-center rounded-md border border-input py-2 font-medium hover:bg-secondary transition"
            >
              {user ? "Request a new link" : "Back to sign in"}
            </Link>
          </div>
        )}

        {status === "error" && (
          <p className="text-sm text-red-500">Something went wrong. Try again.</p>
        )}
      </div>
    </div>
  );
}
