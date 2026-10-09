import { useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Gamepad2, Trash2, UploadCloud } from "lucide-react";
import clsx from "clsx";
import { Empty, PageHeader, Progress, Skeleton, useToast } from "../components/ui";
import { useGameplays } from "../lib/hooks";
import { fmtDuration, fmtSize } from "../lib/format";

function upload(file: File, onProgress: (pct: number) => void): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/gameplays");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress((e.loaded / e.total) * 100);
    xhr.onload = () => {
      if (xhr.status < 300) return resolve();
      try { reject(new Error(JSON.parse(xhr.responseText).detail)); } catch { reject(new Error(`Erro ${xhr.status}`)); }
    };
    xhr.onerror = () => reject(new Error("Falha de rede no envio"));
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

export default function Backgrounds() {
  const games = useGameplays();
  const qc = useQueryClient();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [sending, setSending] = useState<{ name: string; pct: number } | null>(null);
  const [preview, setPreview] = useState<string | null>(null);

  const send = async (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      setSending({ name: f.name, pct: 0 });
      try {
        await upload(f, (pct) => setSending({ name: f.name, pct }));
        toast(`${f.name} enviado`);
      } catch (e) {
        toast(`${f.name}: ${(e as Error).message}`, "bad");
      }
    }
    setSending(null);
    qc.invalidateQueries({ queryKey: ["gameplays"] });
    qc.invalidateQueries({ queryKey: ["env"] });
  };
  const remove = useMutation({
    mutationFn: (id: string) => fetch(`/api/gameplays/${id}`, { method: "DELETE" }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["gameplays"] }); toast("Background removido"); },
  });

  return (
    <>
      <PageHeader title="Backgrounds" subtitle="Vídeos de fundo usados nas narrações. Ficam guardados no servidor." />

      <div onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); send(e.dataTransfer.files); }}
        className={clsx("card flex cursor-pointer flex-col items-center gap-2 border-dashed px-6 py-10 text-center transition", drag && "border-brand bg-brand/5")}
        onClick={() => input.current?.click()} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && input.current?.click()}>
        <UploadCloud className="h-8 w-8 text-brand" />
        <div className="font-semibold">Arraste vídeos aqui ou clique para escolher</div>
        <div className="text-sm text-muted">MP4, MKV, MOV, WEBM ou AVI. Use vídeos livres de direitos ou que você tenha autorização para usar.</div>
        <input ref={input} type="file" accept="video/*" multiple hidden onChange={(e) => e.target.files && send(e.target.files)} />
      </div>
      {sending && (
        <div className="card mt-4 p-4 text-sm">
          <div className="mb-2 flex justify-between"><span className="truncate font-medium">Enviando {sending.name}</span><span>{Math.round(sending.pct)}%</span></div>
          <Progress value={sending.pct} />
        </div>
      )}

      <div className="mt-6">
        {games.isLoading ? <Skeleton className="h-40" /> : (games.data ?? []).length === 0 ? (
          <Empty icon={<Gamepad2 className="h-8 w-8" />} title="Nenhum background ainda">Envie ao menos um vídeo de fundo para criar narrações.</Empty>
        ) : (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {games.data!.map((g) => (
              <div key={g.id} className="card overflow-hidden">
                <button className="block w-full" onClick={() => setPreview(g.id)} aria-label={`Assistir ${g.name}`}>
                  {g.has_thumb ? <img src={`/api/gameplays/${g.id}/thumb`} alt="" className="aspect-video w-full object-cover" />
                    : <div className="grid aspect-video place-items-center bg-raised"><Gamepad2 className="h-8 w-8 text-muted" /></div>}
                </button>
                <div className="flex items-start justify-between gap-2 p-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{g.name}</div>
                    <div className="text-xs text-muted">{fmtDuration(g.duration)} · {fmtSize(g.size)}</div>
                  </div>
                  <button className="btn-ghost !p-1.5" aria-label="Excluir" onClick={() => confirm(`Excluir "${g.name}"?`) && remove.mutate(g.id)}><Trash2 className="h-4 w-4" /></button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      {preview && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/80 p-4" onClick={() => setPreview(null)}>
          <video src={`/api/gameplays/${preview}/file`} controls autoPlay className="max-h-[85vh] max-w-full rounded-xl" onClick={(e) => e.stopPropagation()} />
        </div>
      )}
    </>
  );
}
