import { Link } from "react-router-dom";
import { Gamepad2 } from "lucide-react";
import clsx from "clsx";
import { ChoiceCard, Field, Segmented, Toggle } from "./ui";
import { FORMATS, MODES } from "../lib/labels";
import { useGameplays, useTikTok, useVoices } from "../lib/hooks";
import { defaultTikTok, type Mode, type PresetSpec, type TikTokSpec } from "../lib/types";

type Set = (patch: Partial<PresetSpec>) => void;

/** Passo 1: tipo de vídeo. */
export function ModeStep({ spec, set }: { spec: PresetSpec; set: Set }) {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {(Object.keys(MODES) as Mode[]).map((m) => (
        <ChoiceCard key={m} selected={spec.mode === m} onClick={() => set({ mode: m })} title={MODES[m].title} desc={MODES[m].desc} />
      ))}
    </div>
  );
}

/** Passo 2: conteúdo. */
export function ContentStep({ spec, set }: { spec: PresetSpec; set: Set }) {
  const narrate = spec.mode === "narrate";
  return (
    <div className="space-y-5">
      {narrate && (
        <Field label="Formato">
          <div className="grid gap-3 sm:grid-cols-3">
            {FORMATS.map((f) => (
              <ChoiceCard key={f.id} selected={spec.narrate_format === f.id} onClick={() => set({ narrate_format: f.id })} title={f.title} desc={f.desc} />
            ))}
          </div>
        </Field>
      )}

      <div className="card space-y-4 p-4">
        <Toggle checked={spec.auto_topic} onChange={(v) => set({ auto_topic: v })}
          label="Deixar a IA escolher o tema" hint="A cada vídeo a IA propõe um assunto novo, sem repetir os anteriores." />
        {spec.auto_topic ? (
          <Field label="Nicho do canal (opcional)" hint="Ajuda a IA a ficar no seu assunto. Ex.: ciência e espaço, futebol, dinheiro.">
            <input className="input" value={spec.niche ?? ""} onChange={(e) => set({ niche: e.target.value || null })} placeholder="Qualquer assunto popular" />
          </Field>
        ) : (
          <Field label={spec.mode === "compile" ? "Tema do compilado" : "Tema"}>
            <input className="input" value={(spec.mode === "compile" ? spec.theme : spec.topic) ?? ""}
              onChange={(e) => set(spec.mode === "compile" ? { theme: e.target.value || null } : { topic: e.target.value || null })}
              placeholder={spec.mode === "compile" ? "Top 5 finalizações" : "Buracos negros"} />
          </Field>
        )}
      </div>

      {!narrate && (
        <div className="card space-y-4 p-4">
          <Toggle checked={spec.auto_source} onChange={(v) => set({ auto_source: v })}
            label="Deixar a IA procurar o vídeo" hint="Busca no YouTube, evita vídeos já usados e escolhe o melhor candidato." />
          {!spec.auto_source && (
            <Field label="Link do YouTube ou arquivo" hint="Use somente vídeos que você tem permissão para editar.">
              <input className="input" value={spec.source ?? ""} onChange={(e) => set({ source: e.target.value || null })} placeholder="https://youtube.com/watch?v=…" />
            </Field>
          )}
          {spec.mode === "run" && (
            <Field label="Tipo de conteúdo">
              <select className="input" value={spec.profile} onChange={(e) => set({ profile: e.target.value as PresetSpec["profile"] })}>
                <option value="talk">Com fala (entrevistas, podcasts, histórias)</option>
                <option value="visual">Visual, sem fala (esportes, satisfatórios)</option>
              </select>
            </Field>
          )}
        </div>
      )}

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={narrate ? "Quantos vídeos" : "Quantos cortes"}>
          <input type="number" min={1} max={10} className="input" value={spec.count} onChange={(e) => set({ count: Math.max(1, Math.min(10, Number(e.target.value) || 1)) })} />
        </Field>
        {narrate && (
          <Field label="Duração aproximada" hint={`${spec.target_s} segundos`}>
            <input type="range" min={20} max={120} step={5} className="w-full accent-[rgb(var(--brand))]" value={spec.target_s} onChange={(e) => set({ target_s: Number(e.target.value) })} />
          </Field>
        )}
      </div>
    </div>
  );
}

/** Passo 3: voz e background (só narração). */
export function LookStep({ spec, set }: { spec: PresetSpec; set: Set }) {
  const voices = useVoices();
  const games = useGameplays();
  const toggle = (id: string) =>
    set({ gameplay_ids: spec.gameplay_ids.includes(id) ? spec.gameplay_ids.filter((x) => x !== id) : [...spec.gameplay_ids, id] });

  if (spec.mode !== "narrate")
    return <p className="text-sm text-muted">Os cortes usam o enquadramento e as legendas definidos em Configurações. Nada a ajustar aqui.</p>;
  return (
    <div className="space-y-6">
      <Field label="Voz" hint="Ouça as vozes em Configurações. Vazio usa a voz padrão.">
        <select className="input" value={spec.tts_voice ?? ""} onChange={(e) => set({ tts_voice: e.target.value || null })}>
          <option value="">Voz padrão</option>
          {(voices.data?.known ?? []).map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
      </Field>
      <Field label="Background" hint="Nada selecionado = qualquer background da biblioteca.">
        {(games.data ?? []).length === 0 ? (
          <p className="text-sm text-muted">Você ainda não enviou backgrounds. Envie em <b>Backgrounds</b>.</p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {games.data!.map((g) => (
              <button key={g.id} type="button" onClick={() => toggle(g.id)} aria-pressed={spec.gameplay_ids.includes(g.id)}
                className={clsx("card overflow-hidden text-left transition", spec.gameplay_ids.includes(g.id) ? "border-brand ring-1 ring-brand" : "hover:border-brand/60")}>
                {g.has_thumb ? <img src={`/api/gameplays/${g.id}/thumb`} alt="" className="aspect-video w-full object-cover" />
                  : <div className="grid aspect-video place-items-center bg-raised"><Gamepad2 className="h-6 w-6 text-muted" /></div>}
                <div className="truncate p-2 text-xs font-medium">{g.name}</div>
              </button>
            ))}
          </div>
        )}
      </Field>
    </div>
  );
}

/** Passo "TikTok": postar sozinho ao terminar de gerar. */
export function TikTokStep({ spec, set }: { spec: PresetSpec; set: Set }) {
  const tk = useTikTok();
  const t = spec.tiktok ?? defaultTikTok();
  const up = (p: Partial<TikTokSpec>) => set({ tiktok: { ...t, ...p } });
  const accounts = (tk.data?.accounts ?? []).filter((a) => a.connected);

  if (tk.data && !tk.data.available)
    return <p className="text-sm text-muted">O módulo do TikTok não está instalado neste servidor.</p>;
  if (accounts.length === 0)
    return <p className="text-sm">Conecte uma conta do TikTok em <Link className="underline" to="/configuracoes">Configurações</Link> para postar automaticamente.</p>;
  const delayOptions: [number, string][] = [[0, "Assim que ficar pronto"], [15, "15 minutos depois"], [60, "1 hora depois"], [180, "3 horas depois"], [720, "12 horas depois"], [1440, "1 dia depois"]];
  return (
    <div className="space-y-5">
      <Toggle checked={t.enabled} onChange={(v) => up({ enabled: v, account: v ? (t.account ?? accounts[0].name) : t.account })}
        label="Postar no TikTok ao terminar" hint="Cada vídeo gerado vai para a fila de postagem sozinho. Você acompanha em Execuções." />
      {t.enabled && (
        <>
          <Field label="Conta">
            <select className="input" value={t.account ?? accounts[0].name} onChange={(e) => up({ account: e.target.value })}>
              {accounts.map((a) => <option key={a.name}>{a.name}</option>)}
            </select>
          </Field>
          <Field label="Visibilidade">
            <Segmented value={t.visibility} onChange={(v) => up({ visibility: v, ...(v === "private" ? { delay_min: 0 } : {}) })}
              options={[{ value: "public", label: "Público" }, { value: "private", label: "Só eu" }]} />
            {t.visibility === "private" && <p className="hint">O TikTok não agenda vídeos privados: eles são postados na hora.</p>}
          </Field>
          {t.visibility === "public" && (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Quando postar o 1º vídeo">
                <select className="input" value={delayOptions.some(([m]) => m === t.delay_min) ? t.delay_min : -1} onChange={(e) => up({ delay_min: Number(e.target.value) })}>
                  {delayOptions.map(([m, l]) => <option key={m} value={m}>{l}</option>)}
                  {!delayOptions.some(([m]) => m === t.delay_min) && <option value={-1}>{t.delay_min} minutos depois</option>}
                </select>
              </Field>
              {spec.count > 1 && (
                <Field label="Intervalo entre os vídeos" hint="Postar vários de uma vez parece spam.">
                  <select className="input" value={t.stagger_min} onChange={(e) => up({ stagger_min: Number(e.target.value) })}>
                    {[15, 30, 60, 120, 240, 720].map((m) => <option key={m} value={m}>{m < 60 ? `${m} minutos` : `${m / 60} hora${m > 60 ? "s" : ""}`}</option>)}
                  </select>
                </Field>
              )}
            </div>
          )}
          <div className="space-y-2">
            <Toggle checked={t.caption_ai} onChange={(v) => up({ caption_ai: v })} label="Legenda e hashtags pela IA" hint="Desligado, a legenda é só o título do vídeo." />
            <Toggle checked={t.ai_label} onChange={(v) => up({ ai_label: v })} label="Marcar narrações como conteúdo gerado por IA" hint="Recomendado: a voz é sintética." />
            <Toggle checked={t.allow_comment} onChange={(v) => up({ allow_comment: v })} label="Permitir comentários" />
            <Toggle checked={t.allow_duet} onChange={(v) => up({ allow_duet: v })} label="Permitir duetos" />
            <Toggle checked={t.allow_stitch} onChange={(v) => up({ allow_stitch: v })} label="Permitir stitch" />
          </div>
        </>
      )}
    </div>
  );
}
