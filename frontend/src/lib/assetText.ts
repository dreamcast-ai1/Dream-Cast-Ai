import type { AssetDetail, SceneInfo } from "./types";

export const scenesOf = (a: Pick<AssetDetail, "meta">): SceneInfo[] => (a.meta.scenes as SceneInfo[] | undefined) ?? [];

export const sceneText = (text: string, scene: SceneInfo) => text.slice(scene.start, scene.end).trim();

/** Only the spoken lines of a scene: the quoted text under each character cue. */
export function dialogueOf(sceneBody: string): string {
  const lines = [...sceneBody.matchAll(/^\s*["“](.+?)["”]\s*$/gm)].map((m) => m[1].trim());
  return lines.join(" ");
}

export const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60) || "asset";

export const ASSET_LABEL: Record<string, string> = { STORY: "Story", SCRIPT: "Script", LYRICS: "Lyrics", MUSIC: "Music", VOICE: "Voice", VIDEO: "Video", AVATAR: "Avatar", FACE: "Face" };
export const ASSET_EMOJI: Record<string, string> = { STORY: "📖", SCRIPT: "📝", LYRICS: "✍️", MUSIC: "🎵", VOICE: "🎤", VIDEO: "🎬", AVATAR: "🧑", FACE: "👤" };

export function formatDuration(seconds: number | null): string {
  if (!seconds) return "";
  const m = Math.floor(seconds / 60), s = Math.round(seconds % 60);
  return m ? `${m}:${String(s).padStart(2, "0")}` : `${s}s`;
}
