import { Clapperboard } from "lucide-react";
import { Link, Navigate } from "react-router-dom";
import { GoogleButton } from "../components/GoogleButton";
import { useAuth } from "../context/AuthContext";

const FEATURES = [
  ["🎬", "Video"], ["🎵", "Music"], ["🎤", "Voice"], ["✍️", "Lyrics"], ["📖", "Story"],
  ["📝", "Script"], ["👤", "Face Replacement"], ["🧑", "AI Avatar"], ["💬", "Interactive Avatar"],
];

export default function Landing() {
  const { user, loading } = useAuth();
  if (!loading && user) return <Navigate to="/dashboard" replace />;
  return (
    <div className="min-h-full">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6">
        <span className="flex items-center gap-2 font-display text-lg font-bold"><Clapperboard className="h-5 w-5 text-accent" aria-hidden /> DreamCast<span className="text-accent">AI</span></span>
        <nav className="flex gap-2"><Link to="/login" className="btn-ghost">Sign in</Link><Link to="/register" className="btn-primary">Get started</Link></nav>
      </header>
      <main className="mx-auto max-w-6xl px-4 pb-16 pt-12 text-center sm:px-6 sm:pt-20">
        <p className="mb-4 text-xs font-semibold uppercase tracking-[0.25em] text-accent">AI creative workspace</p>
        <h1 className="mx-auto max-w-3xl text-4xl font-bold leading-tight sm:text-6xl">Create anything. <span className="text-accent">Build your story.</span></h1>
        <p className="mx-auto mt-5 max-w-xl text-base text-muted sm:text-lg">
          One place to write, score, voice and produce your films, songs and characters — organised into projects that remember everything.
        </p>
        <div className="mx-auto mt-8 flex max-w-xs flex-col gap-3">
          <GoogleButton />
          <Link to="/register" className="btn-secondary">Sign up with email</Link>
        </div>
        <ul className="mx-auto mt-16 grid max-w-3xl grid-cols-2 gap-3 sm:grid-cols-3">
          {FEATURES.map(([e, l]) => (
            <li key={l} className="card flex items-center gap-2 px-3 py-3 text-sm font-medium"><span aria-hidden>{e}</span>{l}</li>
          ))}
        </ul>
      </main>
    </div>
  );
}
