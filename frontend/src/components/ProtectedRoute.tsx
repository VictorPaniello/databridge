import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Spinner } from "./Spinner";

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading, needsProfile } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 p-8 text-muted-foreground">
        <Spinner /> Loading…
      </div>
    );
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
  return <>{children}</>;
}
