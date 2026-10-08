import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { ConfigResponse, Env, Gameplay, Job, Preset, Review } from "./types";

export const useEnv = () => useQuery({ queryKey: ["env"], queryFn: () => api<Env>("/env"), refetchInterval: 30000 });
export const useJobs = () =>
  useQuery({
    queryKey: ["jobs"], queryFn: () => api<{ jobs: Job[] }>("/jobs").then((r) => r.jobs),
    refetchInterval: (q) => (q.state.data?.some((j) => j.status === "queued" || j.status === "running") ? 2000 : 10000),
  });
export const useReviews = () =>
  useQuery({ queryKey: ["reviews"], queryFn: () => api<{ reviews: Review[] }>("/reviews").then((r) => r.reviews) });
export const usePresets = () =>
  useQuery({ queryKey: ["presets"], queryFn: () => api<{ presets: Preset[] }>("/presets").then((r) => r.presets) });
export const useGameplays = () =>
  useQuery({ queryKey: ["gameplays"], queryFn: () => api<{ gameplays: Gameplay[] }>("/gameplays").then((r) => r.gameplays) });
export const useConfig = () => useQuery({ queryKey: ["config"], queryFn: () => api<ConfigResponse>("/config") });
export const useVoices = () =>
  useQuery({
    queryKey: ["voices"], staleTime: Infinity,
    queryFn: () => api<{ known: string[]; voice: string; speed: number }>("/voices"),
  });
