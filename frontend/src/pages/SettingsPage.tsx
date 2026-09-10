import { useState } from "react";
import type { FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../api/client";
import { COUNTRY_CODES } from "../data/countryCodes";
import { parsePhone } from "../lib/phone";

export function SettingsPage() {
  const { user, updateProfile } = useAuth();
  const parsed = parsePhone(user?.phone ?? null);
  const [firstName, setFirstName] = useState(user?.first_name ?? "");
  const [lastName, setLastName] = useState(user?.last_name ?? "");
  const [dialCode, setDialCode] = useState(parsed.dialCode);
  const [phoneNumber, setPhoneNumber] = useState(parsed.number);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  if (!user) return null;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    setSubmitting(true);
    try {
      const phone = phoneNumber.trim() ? `${dialCode} ${phoneNumber.trim()}` : undefined;
      await updateProfile({ firstName, lastName, phone });
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-sm">
      <h1 className="text-2xl font-semibold tracking-tight mb-1">Account settings</h1>
      <p className="text-muted-foreground mb-8 text-sm">
        Update the profile details attached to your account.
      </p>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            type="email"
            disabled
            value={user.email}
            className="w-full rounded-md border border-input bg-secondary text-muted-foreground px-3 py-2 outline-none cursor-not-allowed"
          />
        </div>

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

        {error && <p className="text-sm text-red-500">{error}</p>}
        {saved && !error && <p className="text-sm text-primary">Saved.</p>}

        <button
          type="submit"
          disabled={submitting}
          className="rounded-md bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:opacity-90 transition disabled:opacity-50"
        >
          {submitting ? "Saving…" : "Save changes"}
        </button>
      </form>
    </div>
  );
}
