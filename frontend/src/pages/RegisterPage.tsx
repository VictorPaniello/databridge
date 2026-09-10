import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../api/client";
import { ThemeToggle } from "../components/ThemeToggle";
import { COUNTRY_CODES } from "../data/countryCodes";

// Mirrors auth.py's validate_password - client-side hints only, the
// backend re-validates and remains the actual source of truth.
const PASSWORD_RULES = [
  { test: (p: string) => p.length >= 8, label: "At least 8 characters" },
  { test: (p: string) => /[A-Z]/.test(p), label: "One uppercase letter" },
  { test: (p: string) => /[a-z]/.test(p), label: "One lowercase letter" },
  { test: (p: string) => /[0-9]/.test(p), label: "One digit" },
  { test: (p: string) => /[^A-Za-z0-9]/.test(p), label: "One special character" },
];

export function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [dialCode, setDialCode] = useState(COUNTRY_CODES[0].dialCode);
  const [phoneNumber, setPhoneNumber] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const phone = phoneNumber.trim() ? `${dialCode} ${phoneNumber.trim()}` : undefined;
      await register({ email, password, firstName, lastName, phone });
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
      <div className="mx-auto max-w-sm mt-8 pb-16">
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Create an account</h1>
        <p className="text-muted-foreground mb-8 text-sm">Start ingesting your own client data.</p>

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

          <div>
            <label className="block text-sm font-medium mb-1" htmlFor="phone">
              Phone <span className="text-muted-foreground font-normal">(optional)</span>
            </label>
            <div className="flex gap-2">
              <select
                id="phone-country"
                aria-label="Country code"
                value={dialCode}
                onChange={(e) => setDialCode(e.target.value)}
                // bg-background/text-foreground (not bg-transparent) here
                // because the dropdown's own open-list popup is native
                // chrome the page can't reach with Tailwind classes - only
                // color-scheme and an explicit background/color on
                // <option> (below) reliably keep it from falling back to
                // barely-readable default styling in dark mode.
                className="w-28 shrink-0 rounded-md border border-input bg-background text-foreground px-2 py-2 outline-none focus:ring-2 focus:ring-ring"
              >
                {COUNTRY_CODES.map((c) => (
                  <option
                    key={c.iso2}
                    value={c.dialCode}
                    style={{ backgroundColor: "var(--background)", color: "var(--foreground)" }}
                  >
                    {c.iso2} {c.dialCode}
                  </option>
                ))}
              </select>
              <input
                id="phone"
                type="tel"
                autoComplete="tel-national"
                value={phoneNumber}
                onChange={(e) => setPhoneNumber(e.target.value)}
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1" htmlFor="email">
              Email
            </label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 outline-none focus:ring-2 focus:ring-ring"
            />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 outline-none focus:ring-2 focus:ring-ring"
            />
            <ul className="mt-2 space-y-0.5 text-xs">
              {PASSWORD_RULES.map((rule) => {
                const met = rule.test(password);
                return (
                  <li key={rule.label} className={met ? "text-primary" : "text-muted-foreground"}>
                    {met ? "✓" : "○"} {rule.label}
                  </li>
                );
              })}
            </ul>
          </div>

          {error && <p className="text-sm text-red-500">{error}</p>}

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-md bg-primary text-primary-foreground py-2 font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {submitting ? "Creating account…" : "Create account"}
          </button>
        </form>

        <p className="mt-8 text-center text-sm text-muted-foreground">
          Already have an account?{" "}
          <Link to="/login" className="text-ring hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
