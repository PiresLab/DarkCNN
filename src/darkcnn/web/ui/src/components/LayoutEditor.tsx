import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, RotateCcw } from "lucide-react";
import clsx from "clsx";
import { Field, Segmented, useToast } from "./ui";
import { api } from "../lib/api";

type Anchor = "top" | "bottom" | "center";
type LayerDef = { label: string; anchor: Anchor; y: number; size: number; sample: string };
type Kind = "cuts" | "compile" | "narrate";
type Spec = { width: number; height: number; kinds: Record<Kind, string>; layers: Record<Kind, Record<string, LayerDef>>; cuts_variants: Record<string, string[]> };
type Box = { y: number; size: number };
type Layout = Partial<Record<Kind, Record<string, Box>>>;

const W = 270; // largura do quadro do editor (o vídeo tem 1080)
const VARIANTS: { value: string; label: string }[] = [
  { value: "both", label: "Contexto + legenda" },
  { value: "titled", label: "Título + gancho" },
  { value: "captions", label: "Só legenda" },
];
const YELLOW = "#ffff00";
const COLOR: Record<string, string> = { gancho: YELLOW, apresentacao: YELLOW, clipe: YELLOW, numero: YELLOW, selo: YELLOW, contagem: YELLOW };

/** Linhas de exemplo de cada camada (proporções iguais às do vídeo). */
function sampleLines(kind: Kind, name: string, d: LayerDef): { text: string; k: number; color: string }[] {
  const c = COLOR[name] ?? "#ffffff";
  if (kind === "compile" && name === "apresentacao") return [{ text: d.sample, k: 1, color: YELLOW }, { text: "OS 5 MELHORES MOMENTOS · DO #5 AO #1", k: 0.66, color: "#fff" }];
  if (kind === "compile" && name === "clipe") return [{ text: "Mata-leão no último segundo", k: 0.72, color: "#fff" }, { text: d.sample, k: 1, color: YELLOW }];
  if (kind === "narrate" && name === "opcoes") return [{ text: d.sample, k: 1, color: "#fff" }, { text: "OU", k: 50 / 70, color: YELLOW }, { text: "NUNCA SENTIR MEDO", k: 1, color: "#fff" }];
  return [{ text: d.sample, k: 1, color: c }];
}

const outline = (px: number) => {
  const o = Math.max(1, px * 0.08);
  return `${o}px ${o}px 0 #000, -${o}px ${o}px 0 #000, ${o}px -${o}px 0 #000, -${o}px -${o}px 0 #000, 0 ${o * 1.5}px ${o}px rgba(0,0,0,.6)`;
};

/** Editor de posição/tamanho dos textos por tipo de vídeo: arraste no quadro ou ajuste os controles. */
export function LayoutEditor({ saved }: { saved: Layout | undefined }) {
  const spec = useQuery({ queryKey: ["layout"], queryFn: () => api<Spec>("/layout"), staleTime: Infinity });
  const qc = useQueryClient();
  const toast = useToast();
  const [kind, setKind] = useState<Kind>("compile");
  const [variant, setVariant] = useState("both");
  const [draft, setDraft] = useState<Layout>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [png, setPng] = useState<string | null>(null);
  const [loadingPng, setLoadingPng] = useState(false);
  const drag = useRef<{ name: string; startY: number; boxY: number } | null>(null);
  useEffect(() => { setDraft(saved ?? {}); }, [saved]);

  const s = spec.data;
  const scale = s ? W / s.width : 0.25;
  const H = s ? s.height * scale : 480;
  const defs = s?.layers[kind] ?? {};
  const visible = Object.keys(defs).filter((n) => kind !== "cuts" || (s?.cuts_variants[variant] ?? []).includes(n));
  const box = (name: string): Box => draft[kind]?.[name] ?? { y: defs[name].y, size: defs[name].size };
  const setBox = (name: string, b: Partial<Box>) =>
    setDraft((d) => ({ ...d, [kind]: { ...(d[kind] ?? {}), [name]: { ...box(name), ...b } } }));
  const dirty = JSON.stringify(clean(draft, s)) !== JSON.stringify(clean(saved ?? {}, s));

  // prévia real (libass, mesma fonte do vídeo), atualizada um pouco depois de cada mudança
  const body = useMemo(() => JSON.stringify({ kind, variant, text_layout: clean(draft, s) }), [kind, variant, draft, s]);
  useEffect(() => {
    if (!s) return;
    let gone = false;
    const t = setTimeout(async () => {
      setLoadingPng(true);
      try {
        const r = await fetch("/api/layout/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body });
        if (r.ok && !gone) setPng(URL.createObjectURL(await r.blob()));
      } finally { if (!gone) setLoadingPng(false); }
    }, 600);
    return () => { gone = true; clearTimeout(t); };
  }, [body, s]);
  useEffect(() => () => { if (png) URL.revokeObjectURL(png); }, [png]);

  const save = useMutation({
    mutationFn: () => api("/config", { method: "PUT", json: { values: { text_layout: clean(draft, s) } } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["config"] }); toast("Posições salvas"); },
    onError: (e: Error) => toast(e.message, "bad"),
  });

  if (!s) return null;

  const onDown = (name: string) => (e: React.PointerEvent) => {
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { name, startY: e.clientY, boxY: box(name).y };
    setSelected(name);
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    setBox(d.name, { y: Math.round(Math.max(0, Math.min(s.height, d.boxY + (e.clientY - d.startY) / scale))) });
  };
  const onUp = () => { drag.current = null; };

  return (
    <section className="card p-5">
      <h3 className="font-semibold">Posição dos textos</h3>
      <p className="mb-4 text-sm text-muted">Arraste os textos no quadro ou ajuste os controles. A prévia ao lado é gerada pelo mesmo motor que grava os vídeos.</p>
      <div className="mb-4 flex flex-wrap gap-3">
        <Segmented value={kind} onChange={(k) => { setKind(k); setSelected(null); }}
          options={(Object.keys(s.kinds) as Kind[]).map((k) => ({ value: k, label: s.kinds[k] }))} />
        {kind === "cuts" && <Segmented value={variant} onChange={setVariant} options={VARIANTS} />}
      </div>
      <div className="grid gap-6 lg:grid-cols-[auto_auto_1fr]">
        <div>
          <p className="mb-1 text-xs text-muted">Editor</p>
          <div className="relative touch-none select-none overflow-hidden rounded-xl border bg-gradient-to-b from-[#3a4048] to-[#2a2e34]"
            style={{ width: W, height: H }} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
            {visible.map((name) => {
              const d = defs[name];
              const b = box(name);
              const px = b.size * scale;
              const pos: React.CSSProperties = d.anchor === "top" ? { top: b.y * scale } : d.anchor === "bottom" ? { bottom: (s.height - b.y) * scale } : { top: b.y * scale, transform: "translateY(-50%)" };
              const side = name === "selo" ? { left: 50 * scale } : { left: 15 * scale, right: 15 * scale, textAlign: "center" as const };
              return (
                <div key={name} role="slider" aria-label={d.label} aria-valuemin={0} aria-valuemax={s.height} aria-valuenow={b.y} tabIndex={0}
                  onPointerDown={onDown(name)} onFocus={() => setSelected(name)}
                  onKeyDown={(e) => { if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); setBox(name, { y: Math.max(0, Math.min(s.height, b.y + (e.key === "ArrowUp" ? -10 : 10))) }); } }}
                  className={clsx("absolute cursor-ns-resize rounded px-0.5 font-bold leading-tight outline-none", selected === name ? "ring-2 ring-brand" : "hover:ring-1 hover:ring-white/50")}
                  style={{ ...pos, ...side }}>
                  {sampleLines(kind, name, d).map((l, i) => (
                    <div key={i} style={{ fontSize: px * l.k, color: l.color, textShadow: outline(px * l.k) }}>{l.text}</div>
                  ))}
                </div>
              );
            })}
          </div>
        </div>
        <div>
          <p className="mb-1 flex items-center gap-1 text-xs text-muted">Prévia real {loadingPng && <RefreshCw className="h-3 w-3 animate-spin" />}</p>
          <div className="overflow-hidden rounded-xl border bg-raised" style={{ width: W, height: H }}>
            {png ? <img src={png} alt="Prévia real dos textos" className="h-full w-full" /> : <div className="grid h-full place-items-center text-xs text-muted">Gerando…</div>}
          </div>
        </div>
        <div className="space-y-4">
          {visible.map((name) => {
            const d = defs[name];
            const b = box(name);
            const where = d.anchor === "top" ? "topo do texto" : d.anchor === "bottom" ? "base do texto" : "centro do texto";
            return (
              <div key={name} className={clsx("rounded-xl border p-3", selected === name && "border-brand")} onClick={() => setSelected(name)}>
                <div className="mb-2 text-sm font-semibold">{d.label}</div>
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label={`Altura (${where}): ${b.y}px de 1920`}>
                    <input type="range" min={0} max={s.height} step={5} className="w-full accent-[rgb(var(--brand))]" value={b.y} onChange={(e) => setBox(name, { y: Number(e.target.value) })} />
                  </Field>
                  <Field label={`Tamanho: ${b.size}`}>
                    <input type="range" min={20} max={Math.max(120, Math.round(d.size * 2))} step={1} className="w-full accent-[rgb(var(--brand))]" value={b.size} onChange={(e) => setBox(name, { size: Number(e.target.value) })} />
                  </Field>
                </div>
              </div>
            );
          })}
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>Salvar posições</button>
            <button className="btn-secondary" onClick={() => setDraft((d) => { const n = { ...d }; delete n[kind]; return n; })}>
              <RotateCcw className="h-4 w-4" />Padrão de {s.kinds[kind].toLowerCase()}
            </button>
            {dirty && <button className="btn-ghost" onClick={() => setDraft(saved ?? {})}>Descartar</button>}
          </div>
          <p className="hint">Vale para os vídeos gerados depois de salvar. Para refazer um vídeo antigo com o layout novo, gere de novo.</p>
        </div>
      </div>
    </section>
  );
}

/** Só o que difere do padrão vai para o config.yaml. */
function clean(l: Layout, s: Spec | undefined): Layout {
  if (!s) return l;
  const out: Layout = {};
  for (const k of Object.keys(l) as Kind[]) {
    const defs = s.layers[k];
    if (!defs) continue;
    const kept = Object.fromEntries(Object.entries(l[k] ?? {}).filter(([n, b]) => defs[n] && (b.y !== defs[n].y || b.size !== defs[n].size)));
    if (Object.keys(kept).length) out[k] = kept;
  }
  return out;
}
