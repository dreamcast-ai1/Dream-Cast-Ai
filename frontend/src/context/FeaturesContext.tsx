import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "../lib/api";

interface FeatureFlags { generators: Record<string, boolean>; asset_types: Record<string, boolean>; hidden: string[]; languages: string[]; refinement: { ai: boolean; basic: boolean } }
interface FeaturesState {
  /** False until the first answer arrives (guarded screens wait for it, so a hidden feature never flashes on screen). */
  ready: boolean;
  isGeneratorEnabled: (generatorId: string) => boolean;
  isAssetTypeEnabled: (assetType: string) => boolean;
  /** Languages an administrator has switched on (English is always there). */
  languages: string[];
}

const Ctx = createContext<FeaturesState | null>(null);

/** The ONE place the frontend learns which features exist right now. The backend decides (administrator switches stored in the database); a feature that is
 *  switched off is hidden everywhere through these functions, and the API refuses it too. Nothing is hard-coded here. */
export function FeaturesProvider({ children }: { children: ReactNode }) {
  const [flags, setFlags] = useState<FeatureFlags | null>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    api<FeatureFlags>("/api/features").then(setFlags).catch(() => setFlags(null)).finally(() => setReady(true));     // if the call fails nothing is hidden (fail open)
  }, []);
  const value = useMemo<FeaturesState>(() => ({
    ready,
    isGeneratorEnabled: (id) => flags?.generators[id] !== false,
    isAssetTypeEnabled: (type) => flags?.asset_types[type] !== false,
    languages: flags?.languages ?? ["English"],
  }), [flags, ready]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useFeatures() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useFeatures outside FeaturesProvider");
  return v;
}
