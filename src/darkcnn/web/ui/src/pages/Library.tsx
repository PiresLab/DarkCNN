import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Download, Film, RefreshCw, X } from "lucide-react";
import { Badge, Empty, Field, Modal, PageHeader, Skeleton, useToast } from "../components/ui";
import { api, mediaUrl } from "../lib/api";
import { useReviews } from "../lib/hooks";
import { fmtAgo, fmtDuration } from "../lib/format";
import type { Job, Review, ReviewItem } from "../lib/types";

const KIND = { cuts: "Cortes", compilation: "Compilado", narration: "Narração" } as const;
const ST = { approved: { label: "Aprovado", tone: "ok" }, rejected: { label: "Rejeitado", tone: "bad" }, pending: { label: "Para revisar", tone: "muted" } } as const;

export default function Library() {
  const reviews = useReviews();
  const [sel, setSel] = useState<{ r: Review; i: ReviewItem } | null>(null);
  const [filter, setFilter] = useState<"all" | keyof typeof KIND>("all");
  const list = (reviews.data ?? []).filter((r) => filter === "all" || r.kind === filter);

  return (
    <>
      <PageHeader title="Meus vídeos" subtitle="Revise, ajuste e baixe o que foi gerado." />
      <div className="mb-5 flex flex-wrap gap-2">
        {(["all", "narration", "cuts", "compilation"] as const).map((k) => (
          <button key={k} onClick={() => setFilter(k)} className={filter === k ? "btn-primary !py-1.5" : "btn-secondary !py-1.5"}>
            {k === "all" ? "Todos" : KIND[k]}
          </button>
        ))}
      </div>

      {reviews.isLoading ? <Skeleton className="h-48" /> : list.length === 0 ? (
        <Empty icon={<Film className="h-8 w-8" />} title="Nada por aqui ainda">Os vídeos gerados aparecem aqui para você revisar.</Empty>
      ) : (
        <div className="space-y-8">
          {list.map((r) => (
            <section key={r.id}>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <h2 className="font-semibold">{r.title}</h2>
                <Badge tone="info">{KIND[r.kind]}</Badge>
                <span className="text-xs text-muted">{fmtAgo(r.updated)}</span>
              </div>
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
                {r.items.map((i) => {
                  const st = ST[(i.status as keyof typeof ST) in ST ? (i.status as keyof typeof ST) : "pending"];
                  return (
                    <button key={i.rank} onClick={() => setSel({ r, i })} className="card overflow-hidden text-left transition hover:border-brand/60">
                      {i.file ? <video className="aspect-[9/16] w-full bg-black object-cover" muted preload="metadata" src={`${mediaUrl(`${r.id}/${i.file}`)}#t=0.5`} />
                        : <div className="grid aspect-[9/16] place-items-center bg-raised text-xs text-muted">sem arquivo</div>}
                      <div className="space-y-1 p-2.5">
                        <div className="line-clamp-2 text-sm font-medium">{i.title ?? `Vídeo ${i.rank}`}</div>
                        <div className="flex items-center justify-between"><Badge tone={st.tone}>{st.label}</Badge>
                          {i.duration ? <span className="text-xs text-muted">{fmtDuration(i.duration)}</span> : null}</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
      {sel && <ItemModal r={sel.r} item={sel.i} onClose={() => setSel(null)} />}
    </>
  );
}

function ItemModal({ r, item, onClose }: { r: Review; item: ReviewItem; onClose: () => void }) {
  const [f, setF] = useState({ title: item.title ?? "", hook_text: item.hook_text ?? "", start: item.start, end: item.end });
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const editable = r.kind !== "narration";

  const patch = useMutation({
    mutationFn: (body: Record<string, unknown>) => api(`/reviews/${r.id}/items/${item.rank}`, { method: "PATCH", json: body }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["reviews"] }); toast("Salvo"); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const rerender = useMutation({
    mutationFn: () => api<Job>("/jobs", { method: "POST", json: { mode: "render", source: r.source, label: `Refazer: ${r.title}` } }),
    onSuccess: (j) => { qc.invalidateQueries({ queryKey: ["jobs"] }); nav(`/execucoes/${j.id}`); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const url = item.file ? mediaUrl(`${r.id}/${item.file}`) : null;

  return (
    <Modal open onClose={onClose} wide title={item.title ?? `Vídeo ${item.rank}`}
      footer={<>
        <button className="btn-danger" onClick={() => patch.mutate({ status: "rejected" })}><X className="h-4 w-4" />Rejeitar</button>
        <button className="btn-primary" onClick={() => patch.mutate({ status: "approved" })}><Check className="h-4 w-4" />Aprovar</button>
      </>}>
      <div className="grid gap-5 sm:grid-cols-[220px_1fr]">
        <div>
          {url ? <video src={url} controls className="aspect-[9/16] w-full rounded-xl bg-black" /> : <div className="grid aspect-[9/16] place-items-center rounded-xl bg-raised text-sm text-muted">sem arquivo</div>}
          {url && <a className="btn-secondary mt-3 w-full" href={url} download><Download className="h-4 w-4" />Baixar</a>}
        </div>
        <div className="space-y-4">
          {(item.opening_warnings?.length ?? 0) > 0 && (
            <div className="flex gap-2 rounded-xl border border-brand/40 bg-brand/10 p-3 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-brand" /><div>{item.opening_warnings!.join(" · ")}</div>
            </div>
          )}
          {r.kind === "narration" && <p className="rounded-xl bg-raised p-3 text-xs text-muted">Voz sintética: marque o vídeo como conteúdo alterado ao publicar nas plataformas.</p>}
          {editable && (
            <>
              <Field label="Título"><input className="input" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></Field>
              <Field label="Frase de destaque"><input className="input" value={f.hook_text} onChange={(e) => setF({ ...f, hook_text: e.target.value })} /></Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Início (s)"><input type="number" step="0.1" className="input" value={f.start ?? ""} onChange={(e) => setF({ ...f, start: Number(e.target.value) })} /></Field>
                <Field label="Fim (s)"><input type="number" step="0.1" className="input" value={f.end ?? ""} onChange={(e) => setF({ ...f, end: Number(e.target.value) })} /></Field>
              </div>
              <div className="flex flex-wrap gap-2">
                <button className="btn-secondary" disabled={patch.isPending} onClick={() => patch.mutate(f)}>Salvar ajustes</button>
                <button className="btn-secondary" disabled={!r.source || rerender.isPending} onClick={() => rerender.mutate()}
                  title={r.source ? "" : "A fonte desta saída não foi guardada"}><RefreshCw className="h-4 w-4" />Refazer vídeo</button>
              </div>
            </>
          )}
          {r.kind === "narration" && item.lines && (
            <div>
              <div className="label">Roteiro</div>
              <ol className="max-h-64 space-y-1.5 overflow-y-auto rounded-xl border p-3 text-sm">
                {item.lines.map((l, k) => <li key={k} className={l.kind === "escolha" ? "font-semibold text-brand" : ""}>{l.text}</li>)}
              </ol>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
