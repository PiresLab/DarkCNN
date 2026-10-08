import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, KeyRound, Play } from "lucide-react";
import { Badge, Collapsible, Field, PageHeader, Skeleton, Toggle, useToast } from "../components/ui";
import { api, mediaUrl } from "../lib/api";
import { useConfig, useEnv, useVoices } from "../lib/hooks";

type Def =
  | { key: string; label: string; hint?: string; type: "number"; min?: number; max?: number; step?: number }
  | { key: string; label: string; hint?: string; type: "slider"; min: number; max: number; step: number; fmt?: (v: number) => string }
  | { key: string; label: string; hint?: string; type: "select"; options: [string, string][] }
  | { key: string; label: string; hint?: string; type: "toggle" }
  | { key: string; label: string; hint?: string; type: "text" };

const SECTIONS: { title: string; desc: string; fields: Def[]; advanced?: boolean }[] = [
  { title: "Aparência dos vídeos", desc: "Como o vídeo final é enquadrado e legendado.", fields: [
    { key: "layout", label: "Enquadramento", type: "select", options: [["blur", "Vídeo inteiro sobre fundo desfocado"], ["crop", "Cortar o centro (tela cheia)"]] },
    { key: "text_mode", label: "Texto na tela", type: "select", options: [["captions", "Legenda palavra por palavra"], ["both", "Contexto no topo + legenda"], ["titled", "Título no topo + frase embaixo"], ["none", "Sem texto"]] },
    { key: "font", label: "Fonte", type: "text", hint: "Nome de uma fonte instalada no servidor." },
  ] },
  { title: "Narração", desc: "Como os vídeos narrados se comportam.", fields: [
    { key: "tts_speed", label: "Velocidade da voz", type: "slider", min: 0.8, max: 1.5, step: 0.05, fmt: (v) => `${v.toFixed(2)}x`, hint: "1.1 a 1.2 deixa o ritmo mais de vídeo curto." },
    { key: "game_volume", label: "Volume da gameplay", type: "slider", min: 0, max: 1, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%`, hint: "Zero deixa só a voz. O som do jogo pode gerar reclamação de direitos." },
    { key: "countdown_s", label: "Tempo para decidir (\"Você prefere\")", type: "number", min: 0, max: 15, step: 0.5, hint: "Segundos de contagem após cada pergunta." },
    { key: "tick_volume", label: "Volume do tic-tac", type: "slider", min: 0, max: 1, step: 0.05, fmt: (v) => `${Math.round(v * 100)}%` },
  ] },
  { title: "Cortes e compilados", desc: "Duração e quantidade dos cortes.", fields: [
    { key: "clips_per_video", label: "Cortes por vídeo", type: "number", min: 1, max: 12 },
    { key: "min_clip_s", label: "Duração mínima (s)", type: "number", min: 5, max: 180 },
    { key: "max_clip_s", label: "Duração máxima (s)", type: "number", min: 10, max: 180 },
  ] },
  { title: "Inteligência artificial", desc: "Limites de uso do Gemini.", fields: [
    { key: "daily_request_budget", label: "Limite diário de chamadas", type: "number", min: 1, hint: "Trava de segurança para não estourar a cota gratuita." },
    { key: "judge", label: "Revisão extra da IA nos cortes", type: "toggle", hint: "Uma segunda passada compara os candidatos. Melhora a escolha, usa mais chamadas." },
  ] },
  { title: "Avançado", desc: "Só mexa se souber o que está fazendo.", advanced: true, fields: [
    { key: "gemini_model", label: "Modelo do Gemini", type: "text" },
    { key: "tts_model", label: "Modelo de voz", type: "text" },
    { key: "thinking_level", label: "Nível de raciocínio", type: "select", options: [["off", "Desligado"], ["low", "Baixo"], ["medium", "Médio"], ["high", "Alto"]] },
    { key: "whisper_model", label: "Modelo de transcrição", type: "select", options: [["tiny", "tiny (rápido)"], ["base", "base"], ["small", "small (equilibrado)"], ["medium", "medium (preciso, lento)"]] },
    { key: "crf", label: "Qualidade do vídeo (CRF)", type: "number", min: 14, max: 32, hint: "Menor = melhor qualidade e arquivo maior." },
    { key: "preset", label: "Velocidade de codificação", type: "select", options: [["ultrafast", "Muito rápida"], ["veryfast", "Rápida"], ["medium", "Normal"], ["slow", "Lenta (menor arquivo)"]] },
    { key: "max_download_min", label: "Maior vídeo-fonte (min)", type: "number", min: 5 },
  ] },
];

function ApiKeyCard() {
  const env = useEnv();
  const qc = useQueryClient();
  const toast = useToast();
  const [key, setKey] = useState("");
  const save = useMutation({
    mutationFn: () => api("/secrets/gemini", { method: "PUT", json: { key } }),
    onSuccess: () => { setKey(""); qc.invalidateQueries({ queryKey: ["env"] }); toast("Chave salva"); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const remove = useMutation({
    mutationFn: () => api("/secrets/gemini", { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["env"] }); toast("Chave removida"); },
  });
  return (
    <div className="card p-5">
      <div className="mb-1 flex items-center gap-2 font-semibold"><KeyRound className="h-4 w-4 text-brand" />Chave do Gemini
        {env.data?.api_key ? <Badge tone="ok"><CheckCircle2 className="h-3 w-3" />Configurada</Badge> : <Badge tone="bad">Falta configurar</Badge>}</div>
      <p className="mb-4 text-sm text-muted">Crie uma chave gratuita no Google AI Studio e cole abaixo. Ela fica guardada só no seu servidor.</p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input type="password" autoComplete="off" className="input font-mono" placeholder="Cole a chave aqui" value={key} onChange={(e) => setKey(e.target.value)} />
        <button className="btn-primary" disabled={!key.trim() || save.isPending} onClick={() => save.mutate()}>Salvar</button>
        {env.data?.api_key && <button className="btn-secondary" onClick={() => remove.mutate()}>Remover</button>}
      </div>
    </div>
  );
}

function VoiceCard({ values, set }: { values: Record<string, any>; set: (k: string, v: unknown) => void }) {
  const voices = useVoices();
  const toast = useToast();
  const [audio, setAudio] = useState<string | null>(null);
  const sample = useMutation({
    mutationFn: (voice: string) => api<{ file: string }>("/voices/sample", { method: "POST", json: { voice } }),
    onSuccess: (r) => setAudio(`${mediaUrl(r.file)}?t=${Date.now()}`),
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const cur = values.tts_voice ?? "Kore";
  return (
    <div className="card space-y-4 p-5">
      <div><h3 className="font-semibold">Voz padrão</h3><p className="text-sm text-muted">Usada nas narrações quando a receita não escolhe outra.</p></div>
      <Field label="Voz">
        <div className="flex gap-2">
          <select className="input" value={cur} onChange={(e) => set("tts_voice", e.target.value)}>
            {(voices.data?.known ?? [cur]).map((v) => <option key={v}>{v}</option>)}
          </select>
          <button className="btn-secondary shrink-0" disabled={sample.isPending} onClick={() => sample.mutate(cur)}><Play className="h-4 w-4" />Ouvir</button>
        </div>
      </Field>
      {audio && <audio src={audio} controls autoPlay className="w-full" />}
    </div>
  );
}

function FieldInput({ d, value, set }: { d: Def; value: any; set: (v: unknown) => void }) {
  switch (d.type) {
    case "toggle": return <Toggle checked={!!value} onChange={set} label={d.label} hint={d.hint} />;
    case "select":
      return <Field label={d.label} hint={d.hint}><select className="input" value={value ?? ""} onChange={(e) => set(e.target.value)}>
        {d.options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>;
    case "slider":
      return <Field label={`${d.label}: ${(d.fmt ?? String)(Number(value ?? d.min))}`} hint={d.hint}>
        <input type="range" className="w-full accent-[rgb(var(--brand))]" min={d.min} max={d.max} step={d.step} value={value ?? d.min} onChange={(e) => set(Number(e.target.value))} /></Field>;
    case "number":
      return <Field label={d.label} hint={d.hint}><input type="number" className="input" min={d.min} max={d.max} step={d.step ?? 1} value={value ?? ""} onChange={(e) => set(e.target.value === "" ? null : Number(e.target.value))} /></Field>;
    default:
      return <Field label={d.label} hint={d.hint}><input className="input" value={value ?? ""} onChange={(e) => set(e.target.value)} /></Field>;
  }
}

export default function Settings() {
  const cfg = useConfig();
  const qc = useQueryClient();
  const toast = useToast();
  const [values, setValues] = useState<Record<string, any>>({});
  const [dirty, setDirty] = useState<Record<string, unknown>>({});
  useEffect(() => { if (cfg.data) setValues(cfg.data.effective); }, [cfg.data]);

  const save = useMutation({
    mutationFn: () => api("/config", { method: "PUT", json: { values: dirty } }),
    onSuccess: () => { setDirty({}); qc.invalidateQueries({ queryKey: ["config"] }); toast("Configurações salvas"); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const set = (k: string, v: unknown) => { setValues((x) => ({ ...x, [k]: v })); setDirty((x) => ({ ...x, [k]: v })); };

  if (!cfg.data) return <Skeleton className="h-64" />;
  const n = Object.keys(dirty).length;

  return (
    <>
      <PageHeader title="Configurações" subtitle="Valem para todos os vídeos novos. Automações podem sobrescrever a voz e o formato." />
      <div className="space-y-6 pb-24">
        <ApiKeyCard />
        <VoiceCard values={values} set={set} />
        {SECTIONS.map((s) => {
          const body = (
            <div className="grid gap-5 sm:grid-cols-2">
              {s.fields.map((d) => <FieldInput key={d.key} d={d} value={values[d.key]} set={(v) => set(d.key, v)} />)}
            </div>
          );
          return s.advanced ? <Collapsible key={s.title} title={`${s.title} — ${s.desc}`}>{body}</Collapsible> : (
            <section key={s.title} className="card p-5">
              <h3 className="font-semibold">{s.title}</h3><p className="mb-4 text-sm text-muted">{s.desc}</p>{body}
            </section>
          );
        })}
      </div>
      {n > 0 && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-surface/95 p-3 backdrop-blur lg:left-64">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-2 sm:px-6">
            <span className="text-sm text-muted">{n} alteração(ões) não salva(s)</span>
            <div className="flex gap-2">
              <button className="btn-secondary" onClick={() => { setValues(cfg.data!.effective); setDirty({}); }}>Descartar</button>
              <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Salvar</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
