// Agenda <-> texto do backend: cron de 5 campos, `@every Nm|Nh`, ou vários separados por ";".

export type Mode = "interval" | "times" | "advanced";
export interface Sched {
  mode: Mode;
  every: number;               // interval: quantidade
  unit: "m" | "h";             // interval: minutos ou horas
  times: string[];             // times: "HH:MM"
  days: number[];              // times: 0=domingo..6=sábado (vazio = todos)
  raw: string;                 // advanced
}

export const DAY_NAMES = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"];
const pad = (n: number) => String(n).padStart(2, "0");

export const defaultSched = (): Sched => ({ mode: "times", every: 6, unit: "h", times: ["18:00"], days: [], raw: "0 18 * * *" });

function expandDays(field: string): number[] | null {
  if (field === "*") return [];
  const out = new Set<number>();
  for (const part of field.split(",")) {
    const r = /^(\d)(?:-(\d))?$/.exec(part);
    if (!r) return null;
    const a = Number(r[1]), b = r[2] === undefined ? a : Number(r[2]);
    if (a > 7 || b > 7 || b < a) return null;
    for (let d = a; d <= b; d++) out.add(d % 7);
  }
  return [...out].sort();
}

/** Interpreta o texto salvo; o que a tela não sabe montar vira modo avançado, sem perder nada. */
export function parseSchedule(expr: string): Sched {
  const base = { ...defaultSched(), raw: expr };
  const e = /^@every\s+(\d+)\s*([mh])$/i.exec(expr.trim());
  if (e) return { ...base, mode: "interval", every: Number(e[1]), unit: e[2].toLowerCase() as "m" | "h" };

  const times: string[] = [];
  let days: number[] | null = null;
  for (const part of expr.split(";").map((p) => p.trim()).filter(Boolean)) {
    const m = /^(\d{1,2}) ([\d,]+) \* \* (\S+)$/.exec(part);
    const d = m ? expandDays(m[3]) : null;
    if (!m || d === null || Number(m[1]) > 59) return { ...base, mode: "advanced" };
    if (days !== null && days.join() !== d.join()) return { ...base, mode: "advanced" };
    days = d;
    for (const h of m[2].split(",")) {
      if (Number(h) > 23) return { ...base, mode: "advanced" };
      times.push(`${pad(Number(h))}:${pad(Number(m[1]))}`);
    }
  }
  if (!times.length) return { ...base, mode: "advanced" };
  return { ...base, mode: "times", times: [...new Set(times)].sort(), days: days ?? [] };
}

export function buildSchedule(s: Sched): string {
  if (s.mode === "advanced") return s.raw.trim();
  if (s.mode === "interval") return `@every ${Math.max(1, Math.round(s.every))}${s.unit}`;
  const dow = s.days.length && s.days.length < 7 ? [...s.days].sort().join(",") : "*";
  const byMinute = new Map<number, number[]>();
  for (const t of s.times) {
    const [h, m] = t.split(":").map(Number);
    byMinute.set(m, [...(byMinute.get(m) ?? []), h]);
  }
  return [...byMinute.entries()].sort((a, b) => a[0] - b[0])
    .map(([m, hs]) => `${m} ${[...new Set(hs)].sort((a, b) => a - b).join(",")} * * ${dow}`).join(";");
}

/** Texto legível em português para qualquer agenda. */
export function describeSchedule(expr: string): string {
  const s = parseSchedule(expr);
  if (s.mode === "interval") {
    const n = s.every;
    return s.unit === "h" ? `A cada ${n} hora${n > 1 ? "s" : ""}` : n % 60 === 0 ? `A cada ${n / 60} hora${n > 60 ? "s" : ""}` : `A cada ${n} minutos`;
  }
  if (s.mode === "times") {
    const when = s.days.length === 0 || s.days.length === 7 ? "Todo dia"
      : s.days.join() === "1,2,3,4,5" ? "Segunda a sexta" : s.days.join() === "0,6" ? "Fins de semana" : s.days.map((d) => DAY_NAMES[d]).join(", ");
    return `${when} às ${s.times.join(", ")}`;
  }
  return expr;
}
