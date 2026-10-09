import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ImagePlus, KeyRound, Play, RefreshCw, Trash2 } from "lucide-react";
import clsx from "clsx";
import { Badge, Collapsible, Field, PageHeader, Skeleton, Toggle, useToast } from "../components/ui";
import { api, ApiError, mediaUrl } from "../lib/api";
import { TikTokCard } from "../components/TikTokConnect";
import { useConfig, useEnv, useModels, useVoices } from "../lib/hooks";

type Def =
  | { key: string; label: string; hint?: string; type: "number"; min?: number; max?: number; step?: number }
  | { key: string; label: string; hint?: string; type: "slider"; min: number; max: number; step: number; fmt?: (v: number) => string }
  | { key: string; label: string; hint?: string; type: "select"; options: [string, string][] }
  | { key: string; label: string; hint?: string; type: "model"; kind: "text" | "tts" }
  | { key: string; label: string; hint?: string; type: "toggle" }
  | { key: string; label: string; hint?: string; type: "text" };

const WHISPER: [string, string][] = [["tiny", "tiny (muito rápido)"], ["base", "base"], ["small", "small (equilibrado)"], ["medium", "medium (preciso, lento)"], ["large-v3", "large-v3 (melhor, muito lento)"]];

const SECTIONS: { title: string; desc: string; fields: Def[]; advanced?: boolean }[] = [
  { title: "Inteligência artificial", desc: "Modelos usados em cada etapa e limites de uso.", fields: [
    { key: "gemini_model", label: "Modelo de texto (roteiro e análise)", type: "model", kind: "text" },
    { key: "tts_model", label: "Modelo de voz", type: "model", kind: "tts" },
    { key: "whisper_model", label: "Modelo de transcrição", type: "select", options: WHISPER, hint: "Usado só nos cortes de vídeos com fala." },
    { key: "daily_request_budget", label: "Limite diário de chamadas", type: "number", min: 1, hint: "Trava de segurança para não estourar a cota gratuita." },
    { key: "judge", label: "Revisão extra da IA nos cortes", type: "toggle", hint: "Uma segunda passada compara os candidatos. Melhora a escolha, usa mais chamadas." },
  ] },
  { title: "Aparência dos vídeos", desc: "Como o vídeo final é enquadrado e legendado.", fields: [
    { key: "layout", label: "Enquadramento", type: "select", options: [["blur", "Vídeo inteiro sobre fundo desfocado"], ["crop", "Cortar o centro (tela cheia)"]] },
    { key: "text_mode", label: "Texto na tela", type: "select", options: [["captions", "Legenda palavra por palavra"], ["both", "Contexto no topo + legenda"], ["titled", "Título no topo + frase embaixo"], ["none", "Sem texto"]] },
  ] },
  { title: "Narração", desc: "Como os vídeos narrados se comportam.", fields: [
    { key: "tts_speed", label: "Velocidade da voz", type: "slider", min: 0.8, max: 1.5, step: 0.05, fmt: (v) => `${v.toFixed(2)}x`, hint: "1.1 a 1.2 deixa o ritmo mais de vídeo curto." },
    { key: "game_volume", label: "Volume do som do background", type: "slider", min: 0, max: 1, step: 0.01, fmt: (v) => `${Math.round(v * 100)}%`, hint: "Zero deixa só a voz. O som do jogo pode gerar reclamação de direitos." },
  ] },
  { title: "Cortes e compilados", desc: "Duração e quantidade dos cortes.", fields: [
    { key: "clips_per_video", label: "Cortes por vídeo", type: "number", min: 1, max: 12 },
    { key: "min_clip_s", label: "Duração mínima (s)", type: "number", min: 5, max: 180 },
    { key: "max_clip_s", label: "Duração máxima (s)", type: "number", min: 10, max: 180 },
  ] },
  { title: "Avançado", desc: "Só mexa se souber o que está fazendo.", advanced: true, fields: [
    { key: "thinking_level", label: "Nível de raciocínio", type: "select", options: [["off", "Desligado"], ["low", "Baixo"], ["medium", "Médio"], ["high", "Alto"]] },
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
    onSuccess: () => { setKey(""); qc.invalidateQueries({ queryKey: ["env"] }); qc.invalidateQueries({ queryKey: ["models"] }); toast("Chave salva"); },
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

/** Lista os modelos que a chave enxerga; "Outro…" libera digitar um ID à mão (preview muda de nome com frequência). */
function ModelSelect({ d, value, set }: { d: Extract<Def, { type: "model" }>; value: string; set: (v: string) => void }) {
  const models = useModels();
  const qc = useQueryClient();
  const options = models.data?.[d.kind] ?? [];
  const known = options.includes(value);
  const [custom, setCustom] = useState(false);
  const refresh = () => api(`/models?refresh=true`).then((r) => qc.setQueryData(["models"], r));
  const free = custom || (!!value && !known && options.length === 0) || (!!value && !known && !models.isLoading);
  return (
    <Field label={d.label} hint={models.data?.error ? `Não consegui listar os modelos (${models.data.error}). Digite o ID à mão.` : d.hint}>
      <div className="flex gap-2">
        {free ? (
          <input className="input font-mono" value={value ?? ""} onChange={(e) => set(e.target.value)} placeholder="ID do modelo" />
        ) : (
          <select className="input" value={value ?? ""} disabled={models.isLoading}
            onChange={(e) => (e.target.value === "__custom" ? setCustom(true) : set(e.target.value))}>
            {models.isLoading && <option>Carregando…</option>}
            {options.map((m) => <option key={m} value={m}>{m}</option>)}
            <option value="__custom">Outro…</option>
          </select>
        )}
        <button type="button" className="btn-secondary shrink-0 !px-3" title="Atualizar a lista" aria-label="Atualizar a lista de modelos"
          onClick={() => { setCustom(false); refresh(); }}><RefreshCw className={clsx("h-4 w-4", models.isFetching && "animate-spin")} /></button>
      </div>
    </Field>
  );
}

function FieldInput({ d, value, set }: { d: Def; value: any; set: (v: unknown) => void }) {
  switch (d.type) {
    case "toggle": return <Toggle checked={!!value} onChange={set} label={d.label} hint={d.hint} />;
    case "model": return <ModelSelect d={d} value={value} set={set} />;
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

// ---------------------------------------------------------------- marca d'água
type Wm = { path?: string; opacity: number; x: string; y: string; width_pct?: number | null };
type Pos = [number, number]; // coluna, linha: 0 início, 1 centro, 2 fim

const xExpr = (c: number, m: number) => (c === 0 ? `${m}` : c === 1 ? "(W-w)/2" : `W-w-${m}`);
const yExpr = (r: number, m: number) => (r === 0 ? `${m}` : r === 1 ? "(H-h)/2" : `H-h-${m}`);

function readPos(w: Wm): { pos: Pos | null; margin: number } {
  const mx = /^(\d+)$|^W-w-(\d+)$/.exec(w.x);
  const my = /^(\d+)$|^H-h-(\d+)$/.exec(w.y);
  const col = w.x === "(W-w)/2" ? 1 : mx?.[1] !== undefined ? 0 : mx ? 2 : -1;
  const row = w.y === "(H-h)/2" ? 1 : my?.[1] !== undefined ? 0 : my ? 2 : -1;
  const margin = Number(mx?.[1] ?? mx?.[2] ?? my?.[1] ?? my?.[2] ?? 48);
  return { pos: col >= 0 && row >= 0 ? [col, row] : null, margin };
}

function WatermarkCard({ saved }: { saved: Wm | undefined }) {
  const qc = useQueryClient();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [wm, setWm] = useState<Wm>({ opacity: 0.6, x: "W-w-48", y: "120", width_pct: null });
  const [dims, setDims] = useState<{ width: number; height: number } | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => { if (saved) setWm({ width_pct: null, ...saved }); }, [saved]);
  const info = () => api<{ exists: boolean; width?: number; height?: number; version?: number }>("/watermark").then((r) => {
    setDims(r.exists ? { width: r.width!, height: r.height! } : null);
    setVersion(r.version ?? 0);
  });
  useEffect(() => { info(); }, [saved?.path]); // eslint-disable-line react-hooks/exhaustive-deps

  const upload = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return fetch("/api/watermark", { method: "POST", body: form }).then(async (r) => {
        if (!r.ok) throw new ApiError((await r.json().catch(() => ({}))).detail ?? `Erro ${r.status}`);
      });
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["config"] }); info(); toast("Marca d'água enviada"); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const remove = useMutation({
    mutationFn: () => api("/watermark", { method: "DELETE" }),
    onSuccess: () => { setDims(null); qc.invalidateQueries({ queryKey: ["config"] }); toast("Marca d'água removida"); },
  });
  const save = useMutation({
    mutationFn: () => api("/config", { method: "PUT", json: { values: { watermark: wm } } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["config"] }); toast("Marca d'água salva"); },
    onError: (e: Error) => toast(e.message, "bad"),
  });

  const { pos, margin } = readPos(wm);
  const place = (p: Pos, m = margin) => setWm({ ...wm, x: xExpr(p[0], m), y: yExpr(p[1], m) });
  const has = !!dims;
  // prévia em um quadro 9:16 de 180 px de largura (1/6 do vídeo de 1080)
  const k = 180 / 1080;
  const wPx = has ? (wm.width_pct ? (180 * wm.width_pct) / 100 : dims!.width * k) : 0;
  const hPx = has ? (wPx * dims!.height) / dims!.width : 0;
  const m = margin * k;
  const left = pos ? (pos[0] === 0 ? m : pos[0] === 1 ? (180 - wPx) / 2 : 180 - wPx - m) : 180 - wPx - 48 * k;
  const top = pos ? (pos[1] === 0 ? m : pos[1] === 1 ? (320 - hPx) / 2 : 320 - hPx - m) : 120 * k;

  return (
    <section className="card p-5">
      <h3 className="font-semibold">Marca d'água</h3>
      <p className="mb-4 text-sm text-muted">Uma imagem (de preferência PNG com fundo transparente) aplicada em todos os vídeos.</p>
      <div className="grid gap-6 sm:grid-cols-[200px_1fr]">
        <div>
          <div className="relative mx-auto h-[320px] w-[180px] overflow-hidden rounded-xl border bg-gradient-to-b from-raised to-bg">
            {has && <img src={`/api/watermark/file?v=${version}`} alt="Prévia da marca d'água" draggable={false}
              style={{ position: "absolute", left, top, width: wPx, height: hPx, opacity: wm.opacity }} />}
            {!has && <div className="absolute inset-0 grid place-items-center px-4 text-center text-xs text-muted">Nenhuma marca d'água</div>}
          </div>
        </div>
        <div className="space-y-5">
          <div className="flex flex-wrap gap-2">
            <button className="btn-secondary" disabled={upload.isPending} onClick={() => input.current?.click()}><ImagePlus className="h-4 w-4" />{has ? "Trocar imagem" : "Enviar imagem"}</button>
            {has && <button className="btn-ghost" onClick={() => remove.mutate()}><Trash2 className="h-4 w-4" />Remover</button>}
            <input ref={input} type="file" accept="image/png,image/webp,image/jpeg" hidden
              onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
          </div>
          {has && (
            <>
              <Field label="Posição">
                <div className="inline-grid grid-cols-3 gap-1.5" role="radiogroup" aria-label="Posição da marca d'água">
                  {[0, 1, 2].flatMap((r) => [0, 1, 2].map((c) => {
                    const on = pos?.[0] === c && pos?.[1] === r;
                    return <button key={`${c}${r}`} type="button" role="radio" aria-checked={on} aria-label={`Linha ${r + 1}, coluna ${c + 1}`}
                      onClick={() => place([c, r])} className={clsx("h-8 w-10 rounded-lg border transition", on ? "border-brand bg-brand/30" : "bg-raised hover:border-brand/60")} />;
                  }))}
                </div>
              </Field>
              <Field label={`Distância da borda: ${margin}px`}>
                <input type="range" min={0} max={300} step={4} className="w-full accent-[rgb(var(--brand))]" value={margin}
                  onChange={(e) => place(pos ?? [2, 0], Number(e.target.value))} />
              </Field>
              <Field label={`Opacidade: ${Math.round(wm.opacity * 100)}%`}>
                <input type="range" min={0.1} max={1} step={0.05} className="w-full accent-[rgb(var(--brand))]" value={wm.opacity}
                  onChange={(e) => setWm({ ...wm, opacity: Number(e.target.value) })} />
              </Field>
              <Field label={wm.width_pct ? `Tamanho: ${wm.width_pct}% da largura do vídeo` : "Tamanho: original da imagem"}>
                <input type="range" min={5} max={100} step={1} className="w-full accent-[rgb(var(--brand))]" value={wm.width_pct ?? 30}
                  onChange={(e) => setWm({ ...wm, width_pct: Number(e.target.value) })} />
                {wm.width_pct && <button className="btn-ghost mt-1 !px-2 !py-1 text-xs" onClick={() => setWm({ ...wm, width_pct: null })}>Usar tamanho original</button>}
              </Field>
              <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate()}>Salvar marca d'água</button>
            </>
          )}
        </div>
      </div>
    </section>
  );
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
        <TikTokCard />
        <WatermarkCard saved={cfg.data.effective.watermark as Wm | undefined} />
      </div>
      {n > 0 && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t bg-surface/95 p-3 backdrop-blur">
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
