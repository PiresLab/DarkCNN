import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, Send, Sparkles } from "lucide-react";
import { Field, Modal, Segmented, Toggle, useToast } from "./ui";
import { api } from "../lib/api";
import { useTikTok } from "../lib/hooks";
import type { Job, Review, ReviewItem } from "../lib/types";

const pad = (n: number) => String(n).padStart(2, "0");
const localInput = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;

/** Postar um vídeo já gerado: conta, legenda (com sugestão da IA), visibilidade e quando. */
export default function PostModal({ r, item, onClose }: { r: Review; item: ReviewItem; onClose: () => void }) {
  const tk = useTikTok();
  const qc = useQueryClient();
  const toast = useToast();
  const accounts = (tk.data?.accounts ?? []).filter((a) => a.connected);
  const [account, setAccount] = useState("");
  const [caption, setCaption] = useState("");
  const [visibility, setVisibility] = useState<"public" | "private">("public");
  const [when, setWhen] = useState<"now" | "later">("now");
  const [at, setAt] = useState(() => localInput(new Date(Date.now() + 3600_000)));
  const [aiLabel, setAiLabel] = useState(r.kind === "narration");
  const [comment, setComment] = useState(true);
  const [duet, setDuet] = useState(false);
  const [stitch, setStitch] = useState(false);
  const [reuse, setReuse] = useState(true);
  const [remix, setRemix] = useState(true);

  const suggest = useMutation({
    mutationFn: () => api<{ caption: string; ai: boolean; reason?: string }>("/tiktok/caption", { method: "POST", json: { review_id: r.id, rank: item.rank } }),
    onSuccess: (x) => { setCaption(x.caption); if (!x.ai) toast("Sem IA agora: usei o título. " + (x.reason ?? ""), "bad"); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const delay = when === "later" ? Math.round((new Date(at).getTime() - Date.now()) / 1000) : null;
  const delayErr = when === "later" && visibility === "private" ? "O TikTok não agenda vídeos privados."
    : when === "later" && delay !== null && (delay < 900 || delay > 864000) ? "Agende entre 15 minutos e 10 dias à frente." : null;
  const post = useMutation({
    mutationFn: () => api<Job>("/tiktok/post", { method: "POST", json: {
      review_id: r.id, rank: item.rank,
      options: { account: account || accounts[0]?.name, caption, visibility, schedule_s: delay, ai_label: aiLabel, allow_comment: comment, allow_duet: duet, allow_stitch: stitch, allow_content_reuse: reuse, allow_ai_remix: remix },
    } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["tiktok-posts"] }); qc.invalidateQueries({ queryKey: ["jobs"] }); toast("Enviando para o TikTok…"); onClose(); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const ready = accounts.length > 0 && caption.trim().length > 0 && !delayErr;

  return (
    <Modal open onClose={onClose} title="Postar no TikTok"
      footer={<><button className="btn-secondary" onClick={onClose}>Cancelar</button>
        <button className="btn-primary" disabled={!ready || post.isPending} onClick={() => post.mutate()}>
          {post.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}{when === "later" ? "Agendar" : "Postar agora"}</button></>}>
      {tk.data && !tk.data.available ? (
        <p className="text-sm">O módulo do TikTok não está instalado neste servidor.</p>
      ) : accounts.length === 0 ? (
        <p className="text-sm">Nenhuma conta do TikTok conectada. <Link className="underline" to="/configuracoes" onClick={onClose}>Conectar em Configurações</Link>.</p>
      ) : (
        <div className="space-y-5">
          {accounts.length > 1 && (
            <Field label="Conta">
              <select className="input" value={account || accounts[0].name} onChange={(e) => setAccount(e.target.value)}>
                {accounts.map((a) => <option key={a.name}>{a.name}</option>)}
              </select>
            </Field>
          )}
          <Field label="Legenda e hashtags" hint={`${caption.length}/2200`}>
            <textarea className="input min-h-28" value={caption} maxLength={2200} onChange={(e) => setCaption(e.target.value)} placeholder="Escreva ou peça uma sugestão à IA" />
            <button className="btn-secondary mt-2" disabled={suggest.isPending} onClick={() => suggest.mutate()}>
              {suggest.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}Sugerir com IA</button>
          </Field>
          <Field label="Visibilidade">
            <Segmented value={visibility} onChange={setVisibility} options={[{ value: "public", label: "Público" }, { value: "private", label: "Só eu" }]} />
          </Field>
          <Field label="Quando">
            <Segmented value={when} onChange={setWhen} options={[{ value: "now", label: "Agora" }, { value: "later", label: "Agendar" }]} />
            {when === "later" && (
              <div className="mt-2"><input type="datetime-local" className="input !w-64" value={at} min={localInput(new Date(Date.now() + 900_000))} max={localInput(new Date(Date.now() + 864_000_000))} onChange={(e) => setAt(e.target.value)} />
                {delayErr && <p className="mt-1 text-xs text-bad">{delayErr}</p>}</div>
            )}
          </Field>
          <div className="space-y-2">
            <Toggle checked={aiLabel} onChange={setAiLabel} label="Marcar como conteúdo gerado por IA" hint={r.kind === "narration" ? "Recomendado: a narração usa voz sintética." : "Use se o vídeo tiver partes geradas por IA."} />
            <Toggle checked={comment} onChange={setComment} label="Permitir comentários" />
            <Toggle checked={duet} onChange={setDuet} label="Permitir duetos" />
            <Toggle checked={stitch} onChange={setStitch} label="Permitir stitch" />
            <Toggle checked={reuse} onChange={setReuse} label="Permitir reutilização do conteúdo" />
            <Toggle checked={remix} onChange={setRemix} label="Permitir remix com IA" />
          </div>
        </div>
      )}
    </Modal>
  );
}
