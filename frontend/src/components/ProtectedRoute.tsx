import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading, needsProfile, needsVerification } = useAuth();
  const location = useLocation();

  if (loading) {
    return <div className="p-8 text-center text-muted-foreground">Loading…</div>;
  }
  if (!user) {
    return <Navigate to="/login" replace />;
  }
  // Signed in but missing first_name - always a GitHub OAuth signup (see
  // AuthContext's needsProfile). Nothing else in the app is reachable
  // until this is filled in, /complete-profile itself excepted or this
  // would redirect to itself.
  if (needsProfile && location.pathname !== "/complete-profile") {
    return <Navigate to="/complete-profile" replace />;
  }
  // Checked after needsProfile, not before - a GitHub signup fills in
  // its profile first, though in practice it never hits this gate at all
  // (is_verified_by_default=True on that router - see main.py). No
  // exceptions beyond /verify-email-pending itself - not even Settings;
  // PATCH /users/me enforces the same thing server-side (see auth.py's
  // current_verified_active_user), so this isn't just a hidden page,
  // the account genuinely can't be touched until it's verified.
  if (needsVerification && location.pathname !== "/verify-email-pending") {
    return <Navigate to="/verify-email-pending" replace />;
  }
  return <>{children}</>;
}
