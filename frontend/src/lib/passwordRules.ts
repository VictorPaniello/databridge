// Mirrors the backend's UserManager.validate_password (auth.py) -
// client-side hints only, the backend re-validates and remains the actual
// source of truth. Shared by RegisterPage (new account) and SettingsPage
// (changing an existing password) rather than duplicated between them.
export const PASSWORD_RULES = [
  { test: (p: string) => p.length >= 8, label: "At least 8 characters" },
  { test: (p: string) => /[A-Z]/.test(p), label: "One uppercase letter" },
  { test: (p: string) => /[a-z]/.test(p), label: "One lowercase letter" },
  { test: (p: string) => /[0-9]/.test(p), label: "One digit" },
  { test: (p: string) => /[^A-Za-z0-9]/.test(p), label: "One special character" },
];
