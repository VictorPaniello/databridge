import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

// "Hello, {first_name}" - matches the rest of the UI's language. Falls
// back to the email when first_name is unset (every GitHub OAuth signup,
// plus any user who registered before this field existed - see
// UserRead's docstring in the backend's auth.py).
function greeting(user: { email: string; first_name: string | null }) {
  return user.first_name ? `Hello, ${user.first_name}` : user.email;
}

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-slate-200 dark:border-slate-800">
        <div className="mx-auto max-w-5xl px-4 py-3 flex items-center justify-between">
          <Link to="/" className="font-semibold tracking-tight">
            data<span className="text-sky-500">bridge</span>
          </Link>
          {user && (
            <div className="flex items-center gap-4 text-sm">
              <span className="text-slate-500">{greeting(user)}</span>
              <button
                onClick={handleLogout}
                className="rounded-md border border-slate-300 dark:border-slate-700 px-3 py-1.5 hover:bg-slate-100 dark:hover:bg-slate-800 transition"
              >
                Log out
              </button>
            </div>
          )}
        </div>
      </header>
      <main className="flex-1 mx-auto w-full max-w-5xl px-4 py-8">{children}</main>
    </div>
  );
}
