export const fmtDuration = (s: number) => {
  const m = Math.floor(s / 60);
  return m ? `${m}min ${Math.round(s % 60)}s` : `${Math.round(s)}s`;
};
export const fmtSize = (b: number) => (b > 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.round(b / 1e6)} MB`);
export const fmtWhen = (ts: number | null | undefined) =>
  ts ? new Date(ts * 1000).toLocaleString("pt-BR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
export const fmtAgo = (ts: number) => {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return "agora há pouco";
  if (d < 3600) return `há ${Math.floor(d / 60)} min`;
  if (d < 86400) return `há ${Math.floor(d / 3600)} h`;
  return `há ${Math.floor(d / 86400)} d`;
};
