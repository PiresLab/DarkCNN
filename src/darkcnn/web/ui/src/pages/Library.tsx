import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, Film, RefreshCw, Send } from "lucide-react";
import { Badge, Empty, Field, Modal, PageHeader, Skeleton, useToast } from "../components/ui";
import { api, mediaUrl } from "../lib/api";
import { useReviews, useTikTokPosts } from "../lib/hooks";
import PostModal from "../components/PostModal";
import { fmtAgo, fmtDuration, fmtWhen } from "../lib/format";
import type { Job, Review, ReviewItem, TikTokPost } from "../lib/types";

const KIND = { cuts: "Cortes", compilation: "Compilado", narration: "Narração" } as const;

/** Selo do último envio deste vídeo ao TikTok. */
export function PostBadge({ post }: { post: TikTokPost | undefined }) {
  if (!post) return null;
  if (post.status === "done") return <Badge tone="ok">{post.scheduled_for ? `Agendado ${fmtWhen(post.scheduled_for)}` : "Postado"}</Badge>;
  if (post.status === "error") return <Badge tone="bad">{post.uncertain ? "Conferir no TikTok" : "Falha no TikTok"}</Badge>;
  if (post.status === "cancelled") return null;
  return <Badge tone="brand">Enviando…</Badge>;
}

export default function Library() {
  const reviews = useReviews();
  const posts = useTikTokPosts();
  const postOf = (rid: string, file?: string) => posts.data?.find((p) => p.review_id === rid && p.file === file);
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
                  return (
                    <button key={i.rank} onClick={() => setSel({ r, i })} className="card overflow-hidden text-left transition hover:border-brand/60">
                      {i.file ? <video className="aspect-[9/16] w-full bg-black object-cover" muted preload="metadata" src={`${mediaUrl(`${r.id}/${i.file}`)}#t=0.5`} />
                        : <div className="grid aspect-[9/16] place-items-center bg-raised text-xs text-muted">sem arquivo</div>}
                      <div className="space-y-1 p-2.5">
                        <div className="line-clamp-2 text-sm font-medium">{i.title ?? `Vídeo ${i.rank}`}</div>
                        {i.duration ? <div className="text-xs text-muted">{fmtDuration(i.duration)}</div> : null}
                        <PostBadge post={postOf(r.id, i.file)} />
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
      {sel && <ItemModal r={sel.r} item={sel.i} post={postOf(sel.r.id, sel.i.file)} onClose={() => setSel(null)} />}
    </>
  );
}

function ItemModal({ r, item, post, onClose }: { r: Review; item: ReviewItem; post: TikTokPost | undefined; onClose: () => void }) {
  const [posting, setPosting] = useState(false);
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
    <Modal open onClose={onClose} wide title={item.title ?? `Vídeo ${item.rank}`}>
      <div className="grid gap-5 sm:grid-cols-[220px_1fr]">
        <div>
          {url ? <video src={url} controls className="aspect-[9/16] w-full rounded-xl bg-black" /> : <div className="grid aspect-[9/16] place-items-center rounded-xl bg-raised text-sm text-muted">sem arquivo</div>}
          {url && <a className="btn-secondary mt-3 w-full" href={url} download><Download className="h-4 w-4" />Baixar</a>}
          {url && <button className="btn-primary mt-2 w-full" onClick={() => setPosting(true)}><Send className="h-4 w-4" />Postar no TikTok</button>}
          {post && <div className="mt-2 space-y-1 text-xs text-muted"><PostBadge post={post} />
            {post.error && <p className="text-bad">{post.error.replace(/^PostError: /, "")}</p>}</div>}
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
      {posting && <PostModal r={r} item={item} onClose={() => setPosting(false)} />}
    </Modal>
  );
}
