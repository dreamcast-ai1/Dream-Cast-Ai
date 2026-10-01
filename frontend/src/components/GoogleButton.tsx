import { useAuth } from "../context/AuthContext";

/** Google sign-in: through this app's own backend (local mode, when the server has Google credentials) or through Supabase. Otherwise shown disabled with an explanation. */
export function GoogleButton() {
  const { config, loginWithGoogle } = useAuth();
  const enabled = config?.provider === "supabase" || !!config?.google_enabled;
  return (
    <div>
      <button type="button" className="btn-secondary w-full" onClick={loginWithGoogle} disabled={!enabled}>
        <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden><path fill="#4285F4" d="M22.5 12.3c0-.8-.1-1.5-.2-2.2H12v4.2h5.9a5 5 0 0 1-2.2 3.3v2.7h3.5c2.1-1.9 3.3-4.7 3.3-8z"/><path fill="#34A853" d="M12 23c3 0 5.5-1 7.3-2.7l-3.5-2.7c-1 .7-2.3 1.1-3.8 1.1-2.9 0-5.4-2-6.3-4.6H2.1v2.8A11 11 0 0 0 12 23z"/><path fill="#FBBC05" d="M5.7 14.1a6.6 6.6 0 0 1 0-4.2V7.1H2.1a11 11 0 0 0 0 9.8z"/><path fill="#EA4335" d="M12 5.4c1.6 0 3.1.6 4.3 1.7l3.1-3.1A11 11 0 0 0 2.1 7.1l3.6 2.8C6.6 7.400 9.100 5.400 12 5.400z"/></svg>
        Continue with Google
      </button>
      {config && !enabled && <p className="mt-1.5 text-xs text-muted">Google sign-in isn't available yet.</p>}
    </div>
  );
}
