import { Route, Routes } from "react-router-dom";
import { RequireAdmin, RequireAuth } from "./components/layout/Guards";
import { AppShell } from "./components/layout/AppShell";
import Account from "./pages/Account";
import Admin from "./pages/Admin";
import AssetDetail from "./pages/AssetDetail";
import { AuthCallback, ForgotPassword, Login, Register, ResetPassword } from "./pages/auth";
import Create from "./pages/Create";
import Dashboard from "./pages/Dashboard";
import History from "./pages/History";
import Library from "./pages/Library";
import JobDetail from "./pages/JobDetail";
import Landing from "./pages/Landing";
import NotFound from "./pages/NotFound";
import Plans from "./pages/Plans";
import ProjectWorkspace from "./pages/ProjectWorkspace";
import Projects from "./pages/Projects";
import Settings from "./pages/Settings";
import Usage from "./pages/Usage";
import Write from "./pages/Write";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/auth/callback" element={<AuthCallback />} />
      <Route element={<RequireAuth />}>
        <Route element={<AppShell />}>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/create/:generator?" element={<Create />} />
          <Route path="/history" element={<History />} />
          <Route path="/library" element={<Library />} />
          <Route path="/write/:mode?" element={<Write />} />
          <Route path="/history/:jobId" element={<JobDetail />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:projectId" element={<ProjectWorkspace />} />
          <Route path="/projects/:projectId/assets/:assetId" element={<AssetDetail />} />
          <Route path="/usage" element={<Usage />} />
          <Route path="/plans" element={<Plans />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/account" element={<Account />} />
          <Route element={<RequireAdmin />}><Route path="/admin" element={<Admin />} /></Route>
          <Route path="*" element={<NotFound />} />
        </Route>
      </Route>
    </Routes>
  );
}
