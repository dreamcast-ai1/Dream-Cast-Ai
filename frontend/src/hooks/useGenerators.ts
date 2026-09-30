import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Generator } from "../lib/types";

let cache: Generator[] | null = null;

/** Generator catalogue comes from the backend so the two never drift. */
export function useGenerators() {
  const [list, setList] = useState<Generator[]>(cache ?? []);
  useEffect(() => {
    if (cache) return;
    api<Generator[]>("/api/generators").then((g) => { cache = g; setList(g); }).catch(() => undefined);
  }, []);
  return list;
}
