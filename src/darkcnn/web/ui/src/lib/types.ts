export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";
export type Mode = "narrate" | "run" | "compile";

export interface Job {
  id: string; mode: string; label: string; status: JobStatus; created: number; started: number | null;
  ended: number | null; review: string | null; error: string | null; lines: number;
  params: Record<string, unknown>; source: string | null; preset_id: string | null; cancel_requested: boolean;
}
export interface JobDetail extends Job { log: string[]; next: number }

export interface Env {
  ffmpeg: boolean; api_key: boolean; gameplays: number; requests_today: number; daily_budget: number;
  tokens_today: number; whisper_model: string;
}
export interface ReviewItem {
  rank: number; status: string; title?: string; start?: number; end?: number; duration?: number;
  hook_text?: string; context?: string; file?: string; voice?: string; score?: number;
  opening_warnings?: string[]; lines?: { text: string; kind: string }[];
}
export interface Review {
  id: string; kind: "cuts" | "compilation" | "narration"; title: string; updated: number;
  source: string | null; has_review_md: boolean; items: ReviewItem[]; rejected: unknown[];
}
export interface Gameplay {
  id: string; name: string; filename: string; duration: number; size: number; has_thumb: boolean; created: number;
}
export interface PresetSpec {
  mode: Mode; narrate_format: string; count: number; target_s: number; niche: string | null;
  auto_topic: boolean; topic: string | null; theme: string | null; auto_source: boolean; source: string | null;
  profile: "talk" | "visual"; tts_voice: string | null; tts_voices_pool: string[]; tts_speed: number | null;
  gameplay_ids: string[]; overrides: Record<string, unknown>;
}
export interface Schedule {
  id: string; preset_id: string; cron: string; enabled: boolean; last_run: number | null; next_run: number | null;
}
export interface Preset { id: string; name: string; spec: PresetSpec; schedule: Schedule | null; created: number }
export interface ConfigResponse { saved: Record<string, unknown>; effective: Record<string, any> }

export const defaultSpec = (mode: Mode = "narrate"): PresetSpec => ({
  mode, narrate_format: "curiosidade", count: 1, target_s: 45, niche: null, auto_topic: true, topic: null,
  theme: null, auto_source: true, source: null, profile: "talk", tts_voice: null, tts_voices_pool: [],
  tts_speed: null, gameplay_ids: [], overrides: {},
});
