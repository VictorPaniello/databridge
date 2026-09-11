import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ApiError } from "../api/client";
import { COUNTRY_CODES } from "../data/countryCodes";
import { parsePhone } from "../lib/phone";
import { PASSWORD_RULES } from "../lib/passwordRules";
import { ConfirmDialog } from "../components/ConfirmDialog";

export function SettingsPage() {
  const { user, updateProfile, deleteAccount } = useAuth();
  const navigate = useNavigate();
  const [confirmingDeleteAccount, setConfirmingDeleteAccount] = useState(false);
  const [deletingAccount, setDeletingAccount] = useState(false);
  const [deleteAccountError, setDeleteAccountError] = useState<string | null>(null);
  const parsed = parsePhone(user?.phone ?? null);
  const [firstName, setFirstName] = useState(user?.first_name ?? "");
  const [lastName, setLastName] = useState(user?.last_name ?? "");
  const [dialCode, setDialCode] = useState(parsed.dialCode);
  const [phoneNumber, setPhoneNumber] = useState(parsed.number);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Kept empty after a successful save either way - not worth holding a
  // plaintext password in state any longer than it takes to submit it.
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSaved, setPasswordSaved] = useState(false);
  const [changingPassword, setChangingPassword] = useState(false);

  if (!user) return null;

  async function handleDeleteAccount() {
    setConfirmingDeleteAccount(false);
    setDeletingAccount(true);
    setDeleteAccountError(null);
    try {
      await deleteAccount();
      navigate("/login");
    } catch (err) {
      setDeleteAccountError(
        err instanceof ApiError ? err.message : "Couldn't delete your account. Try again.",
      );
      setDeletingAccount(false);
    }
  }

  async function handleProfileSubmit(e: FormEvent) {
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

  async function handlePasswordSubmit(e: FormEvent) {
    e.preventDefault();
    setPasswordError(null);
    setPasswordSaved(false);

    if (newPassword !== confirmPassword) {
      setPasswordError("Passwords don't match.");
      return;
    }

    setChangingPassword(true);
    try {
      // first_name/last_name are required by the same PATCH /users/me
      // call, so they're resent unchanged alongside the new password -
      // this doesn't touch phone since it's untouched by this form.
      await updateProfile({
        firstName,
        lastName,
        phone: user?.phone ?? undefined,
        password: newPassword,
      });
      setNewPassword("");
      setConfirmPassword("");
      setPasswordSaved(true);
    } catch (err) {
      setPasswordError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setChangingPassword(false);
    }
  }

  return (
    <div className="max-w-sm space-y-12">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight mb-1">Account settings</h1>
        <p className="text-muted-foreground mb-8 text-sm">
          Update the profile details attached to your account.
        </p>

        <form onSubmit={handleProfileSubmit} className="space-y-4">
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

          {error && <p className="text-sm text-red-600">{error}</p>}
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

      <div>
        <h2 className="text-lg font-semibold tracking-tight mb-1">Change password</h2>
        <p className="text-muted-foreground mb-6 text-sm">
          Leave blank if you don't want to change it.
        </p>

        <form onSubmit={handlePasswordSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1" htmlFor="new-password">
              New password
            </label>
            <input
              id="new-password"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 outline-none focus:ring-2 focus:ring-ring"
            />
            {newPassword && (
              <ul className="mt-2 space-y-0.5 text-xs" aria-live="polite">
                {PASSWORD_RULES.map((rule) => {
                  const met = rule.test(newPassword);
                  return (
                    <li key={rule.label} className={met ? "text-primary" : "text-muted-foreground"}>
                      <span aria-hidden="true">{met ? "✓" : "○"}</span> {rule.label}
                      <span className="sr-only">{met ? " - met" : " - not met yet"}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium mb-1" htmlFor="confirm-password">
              Confirm new password
            </label>
            <input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 outline-none focus:ring-2 focus:ring-ring"
            />
          </div>

          {passwordError && <p className="text-sm text-red-600">{passwordError}</p>}
          {passwordSaved && !passwordError && (
            <p className="text-sm text-primary">Password changed.</p>
          )}

          <button
            type="submit"
            disabled={changingPassword || !newPassword}
            className="rounded-md bg-primary text-primary-foreground px-4 py-2 text-sm font-medium hover:opacity-90 transition disabled:opacity-50"
          >
            {changingPassword ? "Changing…" : "Change password"}
          </button>
        </form>
      </div>

      <div>
        <h2 className="text-lg font-semibold tracking-tight mb-1 text-red-600">Danger zone</h2>
        <p className="text-muted-foreground mb-4 text-sm">
          Permanently deletes your account and every client record, upload history entry, and
          webhook delivery it owns. This cannot be undone.
        </p>

        {deleteAccountError && <p className="mb-3 text-sm text-red-600">{deleteAccountError}</p>}

        <button
          type="button"
          onClick={() => setConfirmingDeleteAccount(true)}
          disabled={deletingAccount}
          className="rounded-md border border-red-300 dark:border-red-900 bg-card text-red-600 dark:text-red-400 px-4 py-2 text-sm shadow-sm hover:shadow hover:bg-red-50 dark:hover:bg-red-950/30 transition disabled:opacity-50"
        >
          {deletingAccount ? "Deleting…" : "Delete account"}
        </button>
      </div>

      <ConfirmDialog
        open={confirmingDeleteAccount}
        title="Delete your account?"
        message="This permanently erases your account and every client record, upload, and webhook delivery it owns. This cannot be undone."
        confirmLabel="Delete account"
        onConfirm={handleDeleteAccount}
        onCancel={() => setConfirmingDeleteAccount(false)}
      />
    </div>
  );
}
