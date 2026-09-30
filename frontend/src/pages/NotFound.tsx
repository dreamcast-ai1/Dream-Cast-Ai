import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div className="flex flex-col items-center py-24 text-center">
      <p className="text-5xl" aria-hidden>🎞️</p>
      <h1 className="mt-4 text-2xl font-semibold">Scene not found</h1>
      <p className="mt-1 text-muted">The page you're looking for doesn't exist.</p>
      <Link to="/dashboard" className="btn-primary mt-6">Back to dashboard</Link>
    </div>
  );
}
