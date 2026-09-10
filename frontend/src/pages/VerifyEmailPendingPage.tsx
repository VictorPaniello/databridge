import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { requestVerifyToken, ApiError } from "../api/client";
import { ThemeToggle } from "../components/ThemeToggle";

// Reached only via ProtectedRoute redirecting a signed-in, profile-
// complete user whose email isn't verified yet here - in practice always
// an email+password signup (GitHub OAuth ones register with
// is_verified_by_default=True, see main.py). Nothing else in the app,
// Settings included, is reachable until the emailed link is clicked (see
// AuthContext's needsVerification) - a typo'd email has no in-app fix;
// logging out and registering again is the only way out of that.
export function VerifyEmailPendingPage() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleResend() {
    if (!user) return;
    setError(null);
    setSending(true);
    try {
      await requestVerifyToken(user.email);
      setSent(true);
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError("Too many attempts. Wait a minute and try again.");
      } else {
        // Any other failure still shows the generic sent state below -
        // the backend's own route answers identically regardless (see
        // requestVerifyToken's comment in api/client.ts).
        setSent(true);
      }
    } finally {
      setSending(false);
    }
  }

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  return (
    <div className="min-h-screen">
      <div className="flex justify-end p-4">
        <ThemeToggle />
      </div>
      <div className="mx-auto max-w-sm mt-8">
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Verify your email</h1>
        <p className="text-muted-foreground mb-8 text-sm">
          We sent a link to <span className="font-medium">{user?.email}</span>. Click it to
          unlock your records - it expires in 24 hours.
        </p>

        {sent && <p className="mb-4 text-sm text-primary">Verification email sent.</p>}
        {error && <p className="mb-4 text-sm text-red-500">{error}</p>}

        <button
          onClick={handleResend}
          disabled={sending}
          className="w-full rounded-md bg-primary text-primary-foreground py-2 font-medium hover:opacity-90 transition disabled:opacity-50"
        >
          {sending ? "Sending…" : "Resend email"}
        </button>

        <button
          onClick={handleLogout}
          className="mt-8 w-full text-center text-sm text-muted-foreground hover:text-foreground transition"
        >
          Log out
        </button>
      </div>
    </div>
  );
}
