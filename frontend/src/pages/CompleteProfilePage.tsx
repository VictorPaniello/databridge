import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../api/client";
import { ThemeToggle } from "../components/ThemeToggle";
import { PhoneInput } from "../components/PhoneInput";
import { COUNTRY_CODES } from "../data/countryCodes";

// Reached only via ProtectedRoute redirecting a signed-in user with no
// first_name here - in practice always a GitHub OAuth signup, since that
// flow bypasses /auth/register's required first_name/last_name entirely.
// Nothing else in the app is reachable until this is filled in (see
// AuthContext's needsProfile).
export function CompleteProfilePage() {
  const { user, updateProfile } = useAuth();
  const navigate = useNavigate();
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [dialCode, setDialCode] = useState(COUNTRY_CODES[0].dialCode);
  const [phoneNumber, setPhoneNumber] = useState("");
  const [agreedToTerms, setAgreedToTerms] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const phone = phoneNumber.trim() ? `${dialCode} ${phoneNumber.trim()}` : undefined;
      await updateProfile({ firstName, lastName, phone });
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
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
        <h1 className="text-2xl font-semibold tracking-tight mb-1">One more step</h1>
        <p className="text-muted-foreground mb-8 text-sm">
          {user?.email} signed in with GitHub, which doesn't share a name or phone number - fill
          those in to continue.
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-sm font-medium mb-1" htmlFor="first-name">
                First name
              </label>
              <input
                id="first-name"
                type="text"
                required
                autoFocus
                autoComplete="given-name"
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1" htmlFor="last-name">
                Last name
              </label>
              <input
                id="last-name"
                type="text"
                required
                autoComplete="family-name"
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          </div>

          <PhoneInput
            dialCode={dialCode}
            phoneNumber={phoneNumber}
            onDialCodeChange={setDialCode}
            onPhoneNumberChange={setPhoneNumber}
          />

          <div className="flex items-start gap-2">
            <input
              id="agree-terms"
              type="checkbox"
              required
              checked={agreedToTerms}
              onChange={(e) => setAgreedToTerms(e.target.checked)}
              className="mt-0.5 size-4 shrink-0 rounded border-input outline-none focus:ring-2 focus:ring-ring"
            />
            <label htmlFor="agree-terms" className="text-sm text-muted-foreground">
              I agree to the{" "}
              <Link to="/terms" target="_blank" className="text-ring hover:underline">
                Terms of service
              </Link>{" "}
              and{" "}
              <Link to="/privacy" target="_blank" className="text-ring hover:underline">
                Privacy policy
              </Link>
              , including that any client data I upload is my own to share.
            </label>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <button
            type="submit"
            disabled={submitting || !agreedToTerms}
            className="w-full rounded-md bg-primary text-primary-foreground py-2 font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Continue"}
          </button>
        </form>
      </div>
    </div>
  );
}
