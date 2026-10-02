import { useEffect, useMemo, useState } from "react";
import { useFeatures } from "../context/FeaturesContext";
import { api } from "../lib/api";
import type { Generator } from "../lib/types";

let cache: Generator[] | null = null;

/** Generator catalogue comes from the backend so the two never drift. Generators that are unavailable right now (no provider configured) are left out. */
export function useGenerators() {
  const { isGeneratorEnabled } = useFeatures();
  const [list, setList] = useState<Generator[]>(cache ?? []);
  useEffect(() => {
    if (cache) return;
    api<Generator[]>("/api/generators").then((g) => { cache = g; setList(g); }).catch(() => undefined);
  }, []);
  return useMemo(() => list.filter((g) => isGeneratorEnabled(g.id)), [list, isGeneratorEnabled]);
}
