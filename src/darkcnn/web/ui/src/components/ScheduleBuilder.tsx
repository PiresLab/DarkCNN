import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import clsx from "clsx";
import { Field, Segmented } from "./ui";
import { api } from "../lib/api";
import { fmtWhen } from "../lib/format";
import { DAY_NAMES, buildSchedule, describeSchedule, parseSchedule, type Mode, type Sched } from "../lib/schedule";

const QUICK_EVERY: [number, "m" | "h"][] = [[15, "m"], [30, "m"], [1, "h"], [2, "h"], [4, "h"], [6, "h"], [12, "h"]];

/** Monta o texto da agenda. `value` é o que o backend guarda; `onChange` devolve o texto novo. */
export default function ScheduleBuilder({ value, onChange }: { value: string; onChange: (expr: string) => void }) {
  const [s, setS] = useState<Sched>(() => parseSchedule(value));
  const expr = useMemo(() => buildSchedule(s), [s]);
  useEffect(() => { if (expr !== value) onChange(expr); }, [expr]); // eslint-disable-line react-hooks/exhaustive-deps

  const patch = (p: Partial<Sched>) => setS((x) => ({ ...x, ...p }));
  const switchMode = (mode: Mode) => setS((x) => mode === "advanced" ? { ...x, mode, raw: buildSchedule(x) } : { ...x, mode });

  const preview = useQuery({
    queryKey: ["schedule-preview", expr], retry: false, staleTime: 60_000,
    queryFn: () => api<{ next: number[] }>("/schedule/preview", { method: "POST", json: { cron: expr, count: 3 } }),
  });

  return (
    <div className="space-y-4">
      <Segmented<Mode> value={s.mode} onChange={switchMode}
        options={[{ value: "times", label: "Horários" }, { value: "interval", label: "Intervalo" }, { value: "advanced", label: "Avançado" }]} />

      {s.mode === "interval" && (
        <>
          <Field label="Repetir a cada">
            <div className="flex gap-2">
              <input type="number" min={1} className="input !w-28" value={s.every}
                onChange={(e) => patch({ every: Math.max(1, Number(e.target.value) || 1) })} aria-label="Quantidade" />
              <select className="input !w-36" value={s.unit} onChange={(e) => patch({ unit: e.target.value as "m" | "h" })} aria-label="Unidade">
                <option value="m">minutos</option><option value="h">horas</option>
              </select>
            </div>
          </Field>
          <div className="flex flex-wrap gap-2">
            {QUICK_EVERY.map(([n, u]) => (
              <button key={`${n}${u}`} type="button" onClick={() => patch({ every: n, unit: u })}
                className={clsx("rounded-full border px-3 py-1 text-xs", s.every === n && s.unit === u ? "border-brand bg-brand/15 text-brand" : "hover:bg-raised")}>
                {u === "m" ? `${n} min` : `${n} h`}
              </button>
            ))}
          </div>
          <p className="hint">Conta a partir da última execução (intervalo mínimo: 5 minutos). Qualquer valor funciona, como 90 minutos.</p>
        </>
      )}

      {s.mode === "times" && (
        <>
          <Field label="Horários">
            <div className="flex flex-wrap gap-2">
              {s.times.map((t, i) => (
                <div key={i} className="flex items-center gap-1 rounded-xl border bg-surface pr-1">
                  <input type="time" className="bg-transparent px-2 py-1.5 text-sm outline-none" value={t} aria-label={`Horário ${i + 1}`}
                    onChange={(e) => e.target.value && patch({ times: s.times.map((x, j) => (j === i ? e.target.value : x)) })} />
                  {s.times.length > 1 && <button type="button" className="btn-ghost !p-1" aria-label="Remover horário" onClick={() => patch({ times: s.times.filter((_, j) => j !== i) })}><X className="h-3.5 w-3.5" /></button>}
                </div>
              ))}
              {s.times.length < 8 && <button type="button" className="btn-secondary !py-1.5" onClick={() => patch({ times: [...s.times, "12:00"] })}><Plus className="h-4 w-4" />Horário</button>}
            </div>
          </Field>
          <Field label="Dias da semana" hint={s.days.length === 0 ? "Nenhum marcado = todos os dias." : undefined}>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Dias da semana">
              {DAY_NAMES.map((n, d) => {
                const on = s.days.includes(d);
                return <button key={d} type="button" aria-pressed={on} onClick={() => patch({ days: on ? s.days.filter((x) => x !== d) : [...s.days, d].sort() })}
                  className={clsx("h-9 w-12 rounded-xl border text-sm font-medium transition", on ? "border-brand bg-brand text-brand-fg" : "hover:bg-raised")}>{n}</button>;
              })}
              <button type="button" className="btn-ghost !px-2 text-xs" onClick={() => patch({ days: [1, 2, 3, 4, 5] })}>Seg–Sex</button>
              <button type="button" className="btn-ghost !px-2 text-xs" onClick={() => patch({ days: [] })}>Todos</button>
            </div>
          </Field>
        </>
      )}

      {s.mode === "advanced" && (
        <Field label="Expressão" hint="Cron de 5 campos (minuto hora dia mês dia-da-semana), @every 90m / @every 6h, ou várias separadas por ponto e vírgula.">
          <input className="input font-mono" value={s.raw} onChange={(e) => patch({ raw: e.target.value })} spellCheck={false} />
        </Field>
      )}

      <div className="rounded-xl bg-raised p-3 text-sm" aria-live="polite">
        <div className="font-medium">{describeSchedule(expr)}</div>
        {preview.isError ? <div className="mt-1 text-bad">{(preview.error as Error).message}</div>
          : preview.data && <div className="mt-1 text-muted">Próximas: {preview.data.next.map((t) => fmtWhen(t)).join(" · ")}</div>}
      </div>
    </div>
  );
}
