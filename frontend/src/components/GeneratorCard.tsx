import { Link } from "react-router-dom";
import type { Generator } from "../lib/types";

export function GeneratorCard({ g }: { g: Generator }) {
  return (
    <Link to={`/create/${g.id}`} className="card group flex items-start gap-3 p-4 transition-colors hover:border-accent/60">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-raised text-xl" aria-hidden>{g.emoji}</span>
      <span className="min-w-0">
        <span className="block font-medium">{g.label}</span>
        <span className="block text-xs text-muted">{g.description}</span>
      </span>
    </Link>
  );
}
