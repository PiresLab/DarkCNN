import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { CalendarClock, Clapperboard, Gamepad2, Home, Library as LibraryIcon, Menu, Moon, Plus, Settings as SettingsIcon, Sun, Timer } from "lucide-react";
import clsx from "clsx";
import { useEnv, useJobs } from "./lib/hooks";
import Dashboard from "./pages/Dashboard";
import Create from "./pages/Create";
import Automations from "./pages/Automations";
import Library from "./pages/Library";
import Gameplays from "./pages/Gameplays";
import Jobs from "./pages/Jobs";
import Settings from "./pages/Settings";

const NAV = [
  { to: "/", label: "Início", icon: Home, end: true },
  { to: "/criar", label: "Criar vídeo", icon: Plus },
  { to: "/automacoes", label: "Automações", icon: CalendarClock },
  { to: "/biblioteca", label: "Meus vídeos", icon: LibraryIcon },
  { to: "/gameplays", label: "Gameplays", icon: Gamepad2 },
  { to: "/execucoes", label: "Execuções", icon: Timer },
  { to: "/configuracoes", label: "Configurações", icon: SettingsIcon },
];

function useTheme() {
  const [theme, setTheme] = useState<string>(() => {
    try { return localStorage.getItem("theme") ?? "system"; } catch { return "system"; }
  });
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", theme);
    try { localStorage.setItem("theme", theme); } catch { /* sem storage */ }
  }, [theme]);
  const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  return { dark, toggle: () => setTheme(dark ? "light" : "dark") };
}

export default function App() {
  const [open, setOpen] = useState(false);
  const { dark, toggle } = useTheme();
  const env = useEnv();
  const jobs = useJobs();
  const active = jobs.data?.filter((j) => j.status === "queued" || j.status === "running").length ?? 0;

  return (
    <div className="flex min-h-full">
      <aside className={clsx("fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r bg-surface p-4 transition-transform lg:static lg:translate-x-0", open ? "translate-x-0" : "-translate-x-full")}>
        <div className="mb-6 flex items-center gap-3 px-2">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-brand text-brand-fg"><Clapperboard className="h-5 w-5" /></div>
          <div className="leading-tight"><div className="font-bold">DarkCNN</div><div className="text-xs text-muted">Studio</div></div>
        </div>
        <nav className="flex flex-1 flex-col gap-1" aria-label="Seções">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} onClick={() => setOpen(false)}
              className={({ isActive }) => clsx("flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition", isActive ? "bg-brand/15 text-brand" : "text-muted hover:bg-raised hover:text-fg")}>
              <Icon className="h-[18px] w-[18px]" />{label}
              {to === "/execucoes" && active > 0 && <span className="ml-auto rounded-full bg-brand px-2 text-xs font-bold text-brand-fg">{active}</span>}
            </NavLink>
          ))}
        </nav>
        <button className="btn-ghost justify-start" onClick={toggle}>{dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}{dark ? "Tema claro" : "Tema escuro"}</button>
      </aside>
      {open && <div className="fixed inset-0 z-30 bg-black/40 lg:hidden" onClick={() => setOpen(false)} />}

      <div className="min-w-0 flex-1">
        <header className="sticky top-0 z-20 flex items-center gap-3 border-b bg-bg/90 px-4 py-3 backdrop-blur lg:hidden">
          <button className="btn-ghost !p-2" onClick={() => setOpen(true)} aria-label="Abrir menu"><Menu className="h-5 w-5" /></button>
          <span className="font-bold">DarkCNN Studio</span>
        </header>
        <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-8 sm:py-8">
          {env.data && (!env.data.api_key || !env.data.ffmpeg) && (
            <div className="mb-6 rounded-xl border border-brand/40 bg-brand/10 p-4 text-sm">
              {!env.data.api_key && <p><b>Falta a chave do Gemini.</b> <NavLink className="underline" to="/configuracoes">Adicionar nas configurações</NavLink> para a IA funcionar.</p>}
              {!env.data.ffmpeg && <p><b>FFmpeg não encontrado.</b> Ele é necessário para montar os vídeos.</p>}
            </div>
          )}
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/criar" element={<Create />} />
            <Route path="/automacoes" element={<Automations />} />
            <Route path="/biblioteca" element={<Library />} />
            <Route path="/gameplays" element={<Gameplays />} />
            <Route path="/execucoes" element={<Jobs />} />
            <Route path="/execucoes/:id" element={<Jobs />} />
            <Route path="/configuracoes" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
