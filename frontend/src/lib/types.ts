export type Role = "USER" | "ADMIN";
export interface User { id: string; email: string; name: string; avatar_url: string | null; auth_provider: string; role: Role; is_active: boolean; created_at: string; last_login_at: string | null }
export interface Project { id: string; title: string; description: string; genre: string; style: string; status: string; thumbnail_url: string | null; meta: Record<string, unknown>; created_at: string; updated_at: string }
export interface ProjectDetail extends Project { counts: Record<string, number> }
export interface Character { id: string; project_id: string; name: string; age: string; description: string; appearance: string; personality: string; clothing: string; image_url: string | null; created_at: string; updated_at: string }
export interface Reference { id: string; project_id: string; type: string; name: string; original_filename: string; mime_type: string; size_bytes: number; url: string; width: number | null; height: number | null; duration_seconds: number | null; created_at: string }
export interface Asset {
  id: string; project_id: string; type: string; title: string; text_preview: string | null; prompt: string; provider: string | null; status: string;
  version: number; lineage_id: string | null; format: string; language: string; mime_type: string; duration_seconds: number | null;
  meta: Record<string, unknown>; job_id: string | null; url: string | null; thumbnail_url: string | null; has_file: boolean; created_at: string; updated_at: string;
}
export interface LibraryItem extends Asset { project_title: string | null; is_movie: boolean }
export interface SceneInfo { number: number; heading: string; start: number; end: number }
export interface AssetDetail extends Asset { text_content: string | null; versions: { id: string; version: number; title: string; created_at: string }[] }
export const TEXT_ASSET_TYPES = ["STORY", "SCRIPT", "LYRICS"];
export const AUDIO_ASSET_TYPES = ["MUSIC", "VOICE"];
export interface JobAsset { id: string; type: string; status: string; text_preview: string | null; url: string | null; thumbnail_url: string | null }
export interface Job {
  id: string; type: string; status: string; stage: string; progress: number | null;
  project_id: string | null; project_title: string | null; original_prompt: string; refined_prompt: string;
  options: Record<string, unknown>; reference_assets: string[]; provider: string | null; simulated: boolean;
  error_code: string | null; error_message: string | null; attempts: number; parent_id: string | null; cancel_requested: boolean;
  output: Record<string, unknown>; assets: JobAsset[];
  created_at: string; updated_at: string; started_at: string | null; completed_at: string | null;
}
export const ACTIVE_STATUSES = ["QUEUED", "PROCESSING", "RETRYING"];

export interface FieldDef { key: string; label: string; kind: "select" | "text" | "textarea" | "duration" | "asset"; choices: string[]; default: string | number | null; help: string; allow_custom: boolean; asset_types: string[] }
export interface GeneratorSchema extends Generator {
  fields: FieldDef[];
  prompt: { label: string; placeholder: string; required: boolean; max_length: number };
  reference: "optional" | "required" | null; reference_label: string; uses_characters: boolean; note: string; ui: "" | "video" | "face";
  available: boolean; unavailable_reason: string | null; simulated: boolean;
  configured: boolean; config_message: string | null; provider_info: Record<string, unknown>;
}
export interface RefineResult {
  refined_prompt: string; structured_prompt: Record<string, unknown>;
  metadata: { method: "llm" | "template" | "previous" | "local" | "none"; label?: string; provider: string | null; model: string | null; warnings: string[]; options: Record<string, unknown>; context_used: Record<string, unknown> };
}
export interface Notification { id: string; type: string; title: string; message: string; is_read: boolean; job_id: string | null; project_id: string | null; asset_id: string | null; created_at: string }
export interface Generator { id: string; label: string; emoji: string; description: string; section: string; available: boolean }
export interface UsageItem { generator: string; label: string; emoji: string; used: number; limit: number; remaining?: number }
export interface PlanInfo {
  id: string; name: string; tagline: string; description: string; price_minor: number; currency: string; billing_period: string; usage_period: string;
  limits: Record<string, number>; features: { max_video_seconds: number; image_to_video: boolean; face_replacement: boolean };
}
export interface CurrentSubscription {
  plan: PlanInfo; payments_enabled: boolean;
  subscription: { plan_id: string; effective_plan_id: string; status: string; started_at: string; expires_at: string | null; payment_provider: string | null };
}
export interface ProviderInfo { name: string; label: string; info: { model?: string; i2v_model?: string | null; durations?: number[]; aspect_ratios?: string[]; image_to_video?: boolean; key_configured?: boolean; languages?: string[] }; capability: string; generators: string[]; configured: boolean; problems: string[]; simulated: boolean; supports_cancel: boolean; enabled: boolean; daily_cap?: number | null; used_today?: number; provider_quota: string }
export interface AdminUser extends User { generations: number; requests: number; plan_id: string; subscription_status: string; subscription_expires_at: string | null; used_today: number; payment_status: string | null }
export interface AdminStats { total_users: number; active_users: number; total_generations: number; failed_generations: number; api_usage: number; by_generator: Record<string, number> }
export interface CheckoutOrder { order_id: string; amount: number; currency: string; key_id: string; plan: { id: string; name: string }; user_email: string; user_name: string }
export interface SceneVideo { asset_id: string; url: string; thumbnail_url: string | null; duration_seconds: number | null }
export interface Scene {
  id: string; project_id: string; number: number; title: string; description: string; script: string; character_ids: string[]; characters: string[];
  visual_prompt: string; duration_seconds: number; status: "DRAFT" | "GENERATING" | "READY" | "FAILED"; video: SceneVideo | null;
  assets: { id: string; version: number; created_at: string; selected: boolean }[];
  job: { id: string; status: string; stage: string; error_message: string | null } | null; notes?: string[];
}
export interface MovieState {
  scenes: { scene_id: string; number: number; title: string; ready: boolean; duration_seconds: number | null }[];
  missing: string[]; can_assemble: boolean; total_seconds: number;
  active_job: { id: string; status: string; stage: string } | null; movie: Asset | null;
}
export interface TextOptions { generators?: Record<string, boolean>;
  genres: string[]; tones: string[]; languages: string[]; lengths: string[]; script_styles: string[]; script_formats: string[]; durations: number[]; configured: boolean;
  limits: { prompt: number; story: number; instructions: number; genre: number; tone: number; style: number; language: number };
}
export interface SavedText { asset_id: string; project_id: string; job_id: string }
export interface TextCharacter { name: string; description: string }
export interface StoryResult {
  kind: "story"; title: string; logline: string; setting: string; characters: TextCharacter[]; beginning: string; middle: string; climax: string; ending: string;
  full_story: string; text: string; word_count: number; language: string; genre: string; tone: string; saved: SavedText | null; model: string; remaining: number;
}
export interface ScriptScene {
  number: number; heading: string; location: string; time: string; action: string; narration: string; dialogue: { speaker: string; line: string }[];
  sound: string; camera: string; transition: string; estimated_seconds: number;
}
export interface ScriptResult {
  kind: "script"; title: string; logline: string; characters: TextCharacter[]; scenes: ScriptScene[]; scene_count: number; estimated_total_seconds: number; text: string;
  word_count: number; language: string; style: string; tone: string; script_format: string; duration_minutes: number | null; saved: SavedText | null; model: string; remaining: number;
}

export interface FeatureItem { id: string; label: string; description: string; kind: "generator" | "language" | "refinement"; enabled: boolean; default: boolean;
  provider: { status: "configured" | "not_configured" | "disabled"; provider: string | null; message?: string } | null }
