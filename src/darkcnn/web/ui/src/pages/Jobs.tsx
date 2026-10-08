import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, CheckCircle2, Circle, Loader2, StopCircle, Timer } from "lucide-react";
import clsx from "clsx";
import { Badge, Collapsible, Empty, PageHeader, Skeleton, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useJobs } from "../lib/hooks";
import { STAGES, STATUS, modeLabel, stageOf } from "../lib/labels";
import { fmtAgo, fmtDuration } from "../lib/format";
import type { JobDetail } from "../lib/types";

export default function Jobs() {
  const { id } = useParams();
  return id ? <Detail id={id} /> : <List />;
}

function List() {
  const jobs = useJobs();
  return (
    <>
      <PageHeader title="Execuções" subtitle="Tudo que foi gerado, manualmente ou pelas automações." />
      {jobs.isLoading ? <Skeleton className="h-40" /> : (jobs.data ?? []).length === 0 ? (
        <Empty icon={<Timer className="h-8 w-8" />} title="Sem execuções">Quando você gerar um vídeo, o andamento aparece aqui.</Empty>
      ) : (
        <div className="card divide-y">
          {jobs.data!.map((j) => (
            <Link key={j.id} to={`/execucoes/${j.id}`} className="flex items-center justify-between gap-3 p-4 hover:bg-raised">
              <div className="min-w-0">
                <div className="truncate font-medium">{j.label}</div>
                <div className="text-xs text-muted">{modeLabel(j.mode)}{j.preset_id ? " · automação" : ""} · {fmtAgo(j.created)}
                  {j.ended && j.started ? ` · levou ${fmtDuration(j.ended - j.started)}` : ""}</div>
              </div>
              <Badge tone={STATUS[j.status].tone}>{STATUS[j.status].label}</Badge>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}

function Detail({ id }: { id: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [lines, setLines] = useState<string[]>([]);
  const cursor = useRef(0);
  const box = useRef<HTMLPreElement>(null);

  const job = useQuery({
    queryKey: ["job", id],
    queryFn: () => api<JobDetail>(`/jobs/${id}?since=${cursor.current}`),
    refetchInterval: (q) => (q.state.data && (q.state.data.status === "queued" || q.state.data.status === "running") ? 1500 : false),
  });
  useEffect(() => {
    const d = job.data;
    if (!d) return;
    if (d.log.length) setLines((l) => [...l, ...d.log]);
    cursor.current = d.next;
  }, [job.data]);
  useEffect(() => { box.current?.scrollTo({ top: box.current.scrollHeight }); }, [lines]);

  const cancel = useMutation({
    mutationFn: () => api(`/jobs/${id}/cancel`, { method: "POST" }),
    onSuccess: () => { toast("Interrupção pedida"); qc.invalidateQueries({ queryKey: ["job", id] }); },
    onError: (e: Error) => toast(e.message, "bad"),
  });

  const j = job.data;
  if (!j) return job.isError ? <Empty title="Execução não encontrada" /> : <Skeleton className="h-64" />;
  const active = j.status === "queued" || j.status === "running";
  const stage = stageOf(lines, j.status);

  return (
    <>
      <Link to="/execucoes" className="mb-3 inline-flex items-center gap-1 text-sm text-muted hover:text-fg"><ArrowLeft className="h-4 w-4" />Execuções</Link>
      <PageHeader title={j.label} subtitle={`${modeLabel(j.mode)} · iniciada ${fmtAgo(j.created)}`}
        actions={<>
          <Badge tone={STATUS[j.status].tone}>{STATUS[j.status].label}</Badge>
          {active && <button className="btn-danger" disabled={j.cancel_requested || cancel.isPending} onClick={() => cancel.mutate()}><StopCircle className="h-4 w-4" />Cancelar</button>}
          {j.status === "done" && <Link to="/biblioteca" className="btn-primary">Ver vídeos</Link>}
        </>} />

      <div className="card mb-5 p-5">
        <ol className="grid gap-3 sm:grid-cols-4">
          {STAGES.map(([name], i) => {
            const done = i < stage, now = i === stage && active;
            return (
              <li key={name} className={clsx("flex items-center gap-2 text-sm", done || now ? "text-fg" : "text-muted")}>
                {done ? <CheckCircle2 className="h-5 w-5 text-ok" /> : now ? <Loader2 className="h-5 w-5 animate-spin text-brand" /> : <Circle className="h-5 w-5" />}
                {name}
              </li>
            );
          })}
        </ol>
        {j.status === "queued" && <p className="mt-3 text-sm text-muted">Aguardando o worker liberar a vez…</p>}
        {j.error && <p className="mt-3 rounded-xl bg-bad/10 p-3 text-sm text-bad">{j.error}</p>}
      </div>

      <Collapsible title={`Detalhes técnicos (${lines.length} linhas)`}>
        <pre ref={box} className="max-h-96 overflow-auto whitespace-pre-wrap rounded-xl bg-raised p-3 font-mono text-xs leading-relaxed">{lines.join("\n") || "Sem registros ainda."}</pre>
      </Collapsible>
    </>
  );
}
