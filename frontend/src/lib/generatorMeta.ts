export const GEN_FILTERS: { id: string; label: string; types: string }[] = [
  { id: "all", label: "All", types: "" },
  { id: "video", label: "Video", types: "video" },
  { id: "image", label: "Image", types: "image" },
  { id: "music", label: "Music", types: "music" },
  { id: "voice", label: "Voice", types: "voice" },
  { id: "story", label: "Story", types: "story" },
  { id: "script", label: "Script", types: "script" },
  { id: "lyrics", label: "Lyrics", types: "lyrics" },
  { id: "face", label: "Face", types: "face_replacement" },
  { id: "avatar", label: "Avatar", types: "ai_avatar,interactive_avatar" },
];
export const EMOJI: Record<string, string> = { video: "🎬", image: "🖼️", music: "🎵", voice: "🎤", lyrics: "✍️", story: "📖", script: "📝", face_replacement: "👤", ai_avatar: "🧑", interactive_avatar: "💬", movie: "🎞️" };
export const LABEL: Record<string, string> = { video: "Video", image: "Image", music: "Music", voice: "Voice", lyrics: "Lyrics", story: "Story", script: "Script", face_replacement: "Face Replacement", ai_avatar: "AI Avatar", interactive_avatar: "Interactive Avatar", movie: "Movie" };
