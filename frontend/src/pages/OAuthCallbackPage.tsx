import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function OAuthCallbackPage() {
  const { loginWithToken } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);
  // Effects run twice under React StrictMode in dev - guards against
  // trying to process the same fragment (and navigating away mid-flight)
  // a second time.
  const handled = useRef(false);

  useEffect(() => {
    if (handled.current) return;
    handled.current = true;

    // The token travels in the URL fragment (see the backend's
    // RedirectTransport docstring for why), so it's read here client-side
    // and never sent to any server as part of this navigation.
    const params = new URLSearchParams(window.location.hash.slice(1));
    const token = params.get("access_token");

    if (!token) {
      setError("GitHub sign-in didn't return a token. Try again.");
      return;
    }

    // Clears the fragment from the address bar immediately - it's a
    // one-time bearer credential, not something that should linger in
    // browser history or be visible if the URL is shared.
    window.history.replaceState(null, "", window.location.pathname);

    // Navigates to "/" unconditionally - ProtectedRoute itself redirects
    // on to /complete-profile if the now-loaded user turns out to have no
    // first_name (every GitHub OAuth signup), so this doesn't need to
    // know or care which case it is.
    loginWithToken(token)
      .then(() => navigate("/", { replace: true }))
      .catch(() => setError("Couldn't complete sign-in. Try again."));
  }, [loginWithToken, navigate]);

  return (
    <div className="mx-auto max-w-sm mt-24 text-center">
      {error ? (
        <p className="text-red-600 text-sm">{error}</p>
      ) : (
        <p className="text-muted-foreground text-sm">Signing you in…</p>
      )}
    </div>
  );
}
