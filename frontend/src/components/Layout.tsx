import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { ThemeToggle } from "./ThemeToggle";

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border">
        <div className="mx-auto max-w-5xl px-4 py-3 flex items-center justify-between">
          <Link to="/" className="font-semibold tracking-tight">
            data<span className="text-ring">bridge</span>
          </Link>
          <div className="flex items-center gap-4 text-sm">
            {user && (
              <Link to="/uploads" className="text-muted-foreground hover:text-foreground transition">
                Upload history
              </Link>
            )}
            {user && (
              <Link
                to="/settings"
                aria-label="Account settings"
                title="Account settings"
                className="rounded-md border border-border p-1.5 hover:bg-secondary transition"
              >
                <ProfileIcon />
              </Link>
            )}
            <ThemeToggle />
            {user && (
              <button
                onClick={handleLogout}
                className="rounded-md border border-border px-3 py-1.5 hover:bg-secondary transition"
              >
                Log out
              </button>
            )}
          </div>
        </div>
      </header>
      <main className="flex-1 mx-auto w-full max-w-5xl px-4 py-8">{children}</main>
    </div>
  );
}

function ProfileIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="8" r="4" />
      <path d="M4 20c0-3.5 3.5-6 8-6s8 2.5 8 6" />
    </svg>
  );
}
