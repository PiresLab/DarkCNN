import { Link } from "react-router-dom";
import { CalendarClock, Plus, Sparkles } from "lucide-react";
import { Badge, Empty, PageHeader, Progress, Skeleton } from "../components/ui";
import { useEnv, useJobs, usePresets, useReviews } from "../lib/hooks";
import { STATUS, modeLabel } from "../lib/labels";
import { fmtAgo, fmtWhen } from "../lib/format";
import { mediaUrl } from "../lib/api";

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="card p-4">
      <div className="text-sm text-muted">{label}</div>
      <div className="mt-1 text-2xl font-bold">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

export default function Dashboard() {
  const env = useEnv();
  const jobs = useJobs();
  const reviews = useReviews();
  const presets = usePresets();

  const upcoming = (presets.data ?? [])
    .filter((p) => p.schedule?.enabled && p.schedule.next_run)
    .sort((a, b) => a.schedule!.next_run! - b.schedule!.next_run!)
    .slice(0, 3);
  const latest = (reviews.data ?? []).flatMap((r) => r.items.filter((i) => i.file).map((i) => ({ r, i }))).slice(0, 6);
  const running = (jobs.data ?? []).filter((j) => j.status === "running" || j.status === "queued");
  const used = env.data ? Math.round((env.data.requests_today / env.data.daily_budget) * 100) : 0;

  return (
    <>
      <PageHeader title="Início" subtitle="O que está acontecendo no seu canal."
        actions={<Link to="/criar" className="btn-primary"><Plus className="h-4 w-4" />Criar vídeo</Link>} />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Stat label="Em andamento" value={running.length} sub={running.length ? "gerando agora" : "nada na fila"} />
        <Stat label="Vídeos prontos" value={(reviews.data ?? []).reduce((n, r) => n + r.items.filter((i) => i.file).length, 0)} />
        <Stat label="Gameplays" value={env.data?.gameplays ?? "—"} />
        <div className="card p-4">
          <div className="text-sm text-muted">Uso da IA hoje</div>
          <div className="mt-1 text-2xl font-bold">{env.data ? `${env.data.requests_today}/${env.data.daily_budget}` : "—"}</div>
          <div className="mt-2"><Progress value={used} /></div>
        </div>
      </div>

      <div className="mt-8 grid gap-6 lg:grid-cols-5">
        <section className="lg:col-span-3">
          <h2 className="mb-3 font-semibold">Últimos vídeos</h2>
          {reviews.isLoading ? <Skeleton className="h-48" /> : latest.length === 0 ? (
            <Empty icon={<Sparkles className="h-8 w-8" />} title="Nenhum vídeo ainda">
              Crie o primeiro vídeo ou configure uma automação.
            </Empty>
          ) : (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {latest.map(({ r, i }) => (
                <Link to="/biblioteca" key={`${r.id}-${i.rank}`} className="card overflow-hidden transition hover:border-brand/60">
                  <video className="aspect-[9/16] w-full bg-black object-cover" muted preload="metadata"
                    src={`${mediaUrl(`${r.id}/${i.file}`)}#t=0.5`} />
                  <div className="p-2.5">
                    <div className="line-clamp-1 text-sm font-medium">{i.title ?? r.title}</div>
                    <div className="text-xs text-muted">{fmtAgo(r.updated)}</div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </section>

        <section className="space-y-6 lg:col-span-2">
          <div>
            <h2 className="mb-3 font-semibold">Próximas automações</h2>
            {upcoming.length === 0 ? (
              <Empty icon={<CalendarClock className="h-8 w-8" />} title="Nada agendado">
                <Link to="/automacoes" className="underline">Criar uma automação</Link> para postar sem esforço.
              </Empty>
            ) : (
              <div className="card divide-y">
                {upcoming.map((p) => (
                  <div key={p.id} className="flex items-center justify-between p-3 text-sm">
                    <span className="font-medium">{p.name}</span>
                    <span className="text-muted">{fmtWhen(p.schedule!.next_run)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div>
            <h2 className="mb-3 font-semibold">Atividade recente</h2>
            <div className="card divide-y">
              {(jobs.data ?? []).slice(0, 5).map((j) => (
                <Link key={j.id} to={`/execucoes/${j.id}`} className="flex items-center justify-between gap-3 p-3 text-sm hover:bg-raised">
                  <span className="min-w-0"><span className="block truncate font-medium">{j.label}</span>
                    <span className="text-xs text-muted">{modeLabel(j.mode)} · {fmtAgo(j.created)}</span></span>
                  <Badge tone={STATUS[j.status].tone}>{STATUS[j.status].label}</Badge>
                </Link>
              ))}
              {(jobs.data ?? []).length === 0 && <p className="p-4 text-sm text-muted">Sem execuções ainda.</p>}
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
