import type { JobStatus, Mode } from "./types";

export const MODES: Record<Mode, { title: string; desc: string; short: string }> = {
  narrate: { title: "Narração sobre gameplay", short: "Narração",
    desc: "A IA escreve o roteiro, narra com voz natural e coloca sobre uma gameplay." },
  run: { title: "Cortes de um vídeo", short: "Cortes",
    desc: "Pega um vídeo longo e separa os melhores momentos em vídeos verticais." },
  compile: { title: "Compilado Top N", short: "Compilado",
    desc: "Monta uma contagem regressiva com os melhores momentos de um tema." },
};
export const modeLabel = (m: string) =>
  (MODES as Record<string, { short: string }>)[m]?.short ?? (m === "auto" ? "Automático" : m === "render" ? "Re-render" : m);

export const FORMATS: { id: string; title: string; desc: string }[] = [
  { id: "curiosidade", title: "Curiosidade", desc: "Um fato surpreendente, explicado de forma envolvente." },
  { id: "voce-prefere", title: "Você prefere", desc: "Dilemas difíceis com contagem regressiva na tela." },
  { id: "e-se", title: "E se…", desc: "Uma hipótese absurda levada a sério." },
];

export type Tone = "ok" | "bad" | "info" | "muted" | "brand";
export const STATUS: Record<JobStatus, { label: string; tone: Tone }> = {
  queued: { label: "Na fila", tone: "muted" },
  running: { label: "Em andamento", tone: "brand" },
  done: { label: "Concluído", tone: "ok" },
  error: { label: "Falhou", tone: "bad" },
  cancelled: { label: "Cancelado", tone: "muted" },
};

export const STAGES: [string, RegExp][] = [
  ["Preparando", /./],
  ["Roteiro e análise", /roteiro|script|transcri|analis|seleci|gemini|tema/i],
  ["Voz", /voz|tts|sintetiz|bloco/i],
  ["Vídeo", /render|gameplay|corte \d|\.mp4|ffmpeg/i],
];
export function stageOf(log: string[], status: string): number {
  if (status === "done") return STAGES.length;
  let idx = 0;
  for (const line of log) STAGES.forEach(([, re], i) => { if (i > 0 && re.test(line)) idx = Math.max(idx, i); });
  return idx;
}

export const CRON_PRESETS: { label: string; cron: string }[] = [
  { label: "Todo dia às 09:00", cron: "0 9 * * *" },
  { label: "Todo dia às 18:00", cron: "0 18 * * *" },
  { label: "Duas vezes ao dia (09:00 e 19:00)", cron: "0 9,19 * * *" },
  { label: "A cada 6 horas", cron: "0 */6 * * *" },
  { label: "Segunda a sexta às 12:00", cron: "0 12 * * 1-5" },
];
export const cronLabel = (cron: string) => CRON_PRESETS.find((c) => c.cron === cron)?.label ?? cron;
