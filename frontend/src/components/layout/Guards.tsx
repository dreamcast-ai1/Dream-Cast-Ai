import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { useFeatures } from "../../context/FeaturesContext";
import { PageLoader } from "../ui/feedback";

export function RequireAuth() {
  const { user, loading } = useAuth();
  const { ready } = useFeatures();            // wait for the feature flags so a hidden feature never flashes on screen
  const loc = useLocation();
  if (loading || !ready) return <PageLoader />;
  if (!user) return <Navigate to="/login" replace state={{ from: loc.pathname + loc.search }} />;
  return <Outlet />;
}

/** UX guard only; the backend independently enforces admin access on every /api/admin route. */
export function RequireAdmin() {
  const { user } = useAuth();
  if (user?.role !== "ADMIN") return <Navigate to="/dashboard" replace />;
  return <Outlet />;
}
