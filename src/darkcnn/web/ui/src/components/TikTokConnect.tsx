import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, Plug, ShieldCheck, Trash2 } from "lucide-react";
import clsx from "clsx";
import { Badge, Collapsible, Field, Modal, useToast } from "./ui";
import { api } from "../lib/api";
import { useConfig, useTikTok } from "../lib/hooks";
import type { LoginView } from "../lib/types";

/** Navegador remoto: mostra a página de login do TikTok (QR code) e repassa cliques e teclas ao servidor. */
function ConnectModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState("");
  const [proxy, setProxy] = useState("");
  const [sid, setSid] = useState<string | null>(null);
  const [text, setText] = useState("");
  const img = useRef<HTMLImageElement>(null);

  const start = useMutation({
    mutationFn: () => api<LoginView>("/tiktok/login", { method: "POST", json: { name, proxy: proxy.trim() || null } }),
    onSuccess: (v) => setSid(v.id),
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const view = useQuery({
    queryKey: ["tiktok-login", sid], enabled: !!sid, refetchIntervalInBackground: true,  // segue ao ir buscar o código no e-mail
    queryFn: () => api<LoginView>(`/tiktok/login/${sid}`),
    refetchInterval: (q) => (q.state.data && !["starting", "waiting"].includes(q.state.data.status) ? false : 600),
  });
  const v = view.data;
  const connected = v?.status === "connected";
  useEffect(() => {
    if (connected) { qc.invalidateQueries({ queryKey: ["tiktok"] }); toast("TikTok conectado"); }
  }, [connected]); // eslint-disable-line react-hooks/exhaustive-deps

  // os comandos saem em fila, um por vez: um código de 6 dígitos não pode chegar embaralhado
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  const send = (path: string, json: unknown) => {
    if (!sid) return;
    chain.current = chain.current.then(() => api(`/tiktok/login/${sid}/${path}`, { method: "POST", json })).catch(() => {});
  };
  const [pulse, setPulse] = useState<{ x: number; y: number; k: number } | null>(null);
  const close = () => { if (sid && v && ["starting", "waiting"].includes(v.status)) api(`/tiktok/login/${sid}`, { method: "DELETE" }).catch(() => {}); onClose(); };
  const onImgClick = (e: React.MouseEvent<HTMLImageElement>) => {
    const r = img.current!.getBoundingClientRect();
    setPulse({ x: e.clientX - r.left, y: e.clientY - r.top, k: Date.now() });  // confirma onde o clique foi
    img.current!.parentElement!.focus();  // a partir daqui as teclas vão para a página remota
    send("click", { x: ((e.clientX - r.left) / r.width) * v!.viewport.width, y: ((e.clientY - r.top) / r.height) * v!.viewport.height });
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key.length === 1) { e.preventDefault(); send("type", { text: e.key }); }
    else if (["Enter", "Backspace", "Tab", "Escape", "Delete"].includes(e.key)) { e.preventDefault(); send("key", { key: e.key }); }
  };
  const onPaste = (e: React.ClipboardEvent) => { const t = e.clipboardData.getData("text"); if (t) { e.preventDefault(); send("type", { text: t }); } };

  return (
    <Modal open onClose={close} wide title="Conectar TikTok"
      footer={connected ? <button className="btn-primary" onClick={onClose}>Concluir</button>
        : <button className="btn-secondary" onClick={close}>Cancelar</button>}>
      {!sid ? (
        <div className="space-y-4">
          <p className="text-sm text-muted">O DarkCNN abre o login do TikTok no servidor e mostra a página aqui. A forma mais segura é o <b>QR code</b>: aponte a câmera do app do TikTok (Perfil → ícone de QR code → Escanear) e nada digitado passa pelo servidor.</p>
          <Field label="Nome desta conta" hint="Só para você reconhecer (ex.: Canal Curiosidades).">
            <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Meu canal" />
          </Field>
          <Collapsible title="Proxy (opcional)">
            <Field label="Proxy da conta" hint="Recomendado se você tem várias contas: cada uma com seu IP. Formatos: http://usuario:senha@host:porta, socks5://…">
              <input className="input font-mono" value={proxy} onChange={(e) => setProxy(e.target.value)} placeholder="http://usuario:senha@host:porta" />
            </Field>
          </Collapsible>
          <button className="btn-primary" disabled={!name.trim() || start.isPending} onClick={() => start.mutate()}>
            {start.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plug className="h-4 w-4" />}Abrir login
          </button>
        </div>
      ) : connected ? (
        <div className="flex flex-col items-center gap-2 py-8 text-center">
          <CheckCircle2 className="h-10 w-10 text-ok" />
          <div className="font-semibold">Conta conectada</div>
          <p className="text-sm text-muted">A sessão ficou salva no servidor. Você não precisa entrar de novo enquanto ela valer.</p>
        </div>
      ) : v?.status === "error" || v?.status === "cancelled" ? (
        <div className="space-y-3">
          <p className="rounded-xl bg-bad/10 p-3 text-sm text-bad">{v.error ?? "Login cancelado."}</p>
          <button className="btn-secondary" onClick={() => { setSid(null); }}>Tentar de novo</button>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-muted">{v?.status === "waiting" ? "Escaneie o QR code com o app do TikTok. Se pedir verificação (código por e-mail), clique no campo aqui na imagem e digite: o teclado vai direto para a página." : "Abrindo o navegador no servidor…"}</p>
          <div tabIndex={0} onKeyDown={onKey} onPaste={onPaste} aria-label="Navegador remoto: clique num campo e digite"
            className="relative mx-auto max-w-[760px] overflow-hidden rounded-xl border bg-black outline-none focus-visible:ring-2 focus-visible:ring-brand">
            {pulse && <span key={pulse.k} className="pointer-events-none absolute z-10 h-6 w-6 -translate-x-1/2 -translate-y-1/2 animate-ping rounded-full border-2 border-brand" style={{ left: pulse.x, top: pulse.y }} />}
            {v?.image ? <img ref={img} src={v.image} alt="Página de login do TikTok" className="block w-full cursor-pointer select-none" onClick={onImgClick} draggable={false} />
              : <div className="grid aspect-[1000/680] place-items-center text-muted"><Loader2 className="h-6 w-6 animate-spin" /></div>}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input className="input !w-64" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ou cole/digite o código aqui"
              onKeyDown={(e) => { if (e.key === "Enter" && text) { send("type", { text }); setText(""); } }} />
            <button className="btn-secondary" disabled={!text} onClick={() => { send("type", { text }); setText(""); }}>Enviar ao campo</button>
            {["Enter", "Backspace", "Tab"].map((k) => <button key={k} className="btn-ghost !px-2 text-xs" onClick={() => send("key", { key: k })}>{k}</button>)}
          </div>
          <p className="flex items-start gap-1.5 text-xs text-muted"><ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />O texto vai para o campo em foco (ou para o primeiro campo visível). Se você digitar e-mail e senha aqui, as teclas passam pelo servidor do DarkCNN (não são gravadas). Prefira o QR code.</p>
        </div>
      )}
    </Modal>
  );
}

const parseTags = (raw: string) => [...new Set(raw.split(/[\s,;]+/).map((t) => t.replace(/^#+/, "").trim()).filter(Boolean))];

/** Hashtags de alcance: 3 delas (sorteadas) entram em cada legenda gerada pela IA, antes das 3 do assunto. */
function BaseTags() {
  const cfg = useConfig();
  const qc = useQueryClient();
  const toast = useToast();
  const saved: string[] = cfg.data?.effective.tiktok_base_tags ?? [];
  const [text, setText] = useState<string | null>(null);
  const value = text ?? saved.map((t) => `#${t}`).join(" ");
  const tags = parseTags(value);
  const save = useMutation({
    mutationFn: () => api("/config", { method: "PUT", json: { values: { tiktok_base_tags: tags } } }),
    onSuccess: () => { setText(null); qc.invalidateQueries({ queryKey: ["config"] }); toast("Hashtags salvas"); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  return (
    <div className="mt-4">
      <Field label="Hashtags de alcance" hint="Entram 3 delas, sorteadas, em cada legenda gerada pela IA, antes das 3 do assunto. A IA não vê o que está em alta: mantenha esta lista atualizada.">
        <div className="flex flex-col gap-2 sm:flex-row">
          <input className="input font-mono" value={value} onChange={(e) => setText(e.target.value)} placeholder="#fy #fyp #foryou #parati #viral" />
          <button className="btn-secondary shrink-0" disabled={text === null || save.isPending} onClick={() => save.mutate()}>Salvar</button>
        </div>
      </Field>
    </div>
  );
}

export function TikTokCard() {
  const tk = useTikTok();
  const qc = useQueryClient();
  const toast = useToast();
  const [connecting, setConnecting] = useState(false);
  const [imp, setImp] = useState({ name: "", sessionid: "", datacenter: "" });
  const [checks, setChecks] = useState<Record<string, boolean | "busy">>({});
  const refresh = () => qc.invalidateQueries({ queryKey: ["tiktok"] });

  const check = async (name: string) => {
    setChecks((c) => ({ ...c, [name]: "busy" }));
    try { const r = await api<{ ok: boolean }>(`/tiktok/accounts/${name}/check`, { method: "POST" }); setChecks((c) => ({ ...c, [name]: r.ok })); }
    catch (e) { toast((e as Error).message, "bad"); setChecks((c) => { const { [name]: _x, ...rest } = c; return rest; }); }
  };
  const remove = useMutation({
    mutationFn: (name: string) => api(`/tiktok/accounts/${name}`, { method: "DELETE" }),
    onSuccess: () => { refresh(); toast("Conta removida"); },
  });
  const importSession = useMutation({
    mutationFn: () => api("/tiktok/import", { method: "POST", json: { name: imp.name, sessionid: imp.sessionid, datacenter: imp.datacenter || null } }),
    onSuccess: () => { setImp({ name: "", sessionid: "", datacenter: "" }); refresh(); toast("Conta importada"); },
    onError: (e: Error) => toast(e.message, "bad"),
  });

  const d = tk.data;
  return (
    <section className="card p-5">
      <div className="mb-1 flex items-center gap-2 font-semibold">TikTok</div>
      <p className="mb-4 text-sm text-muted">Conecte uma conta para postar os vídeos direto do painel, manualmente ou nas automações.</p>

      {d && !d.available && <p className="rounded-xl bg-raised p-3 text-sm">O módulo do TikTok não está instalado neste servidor: <code>pip install "darkcnn[tiktok]"</code>.</p>}
      {d?.available && !d.browser_installed && (
        <p className="mb-4 flex gap-2 rounded-xl border border-brand/40 bg-brand/10 p-3 text-sm"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
          <span>O navegador do servidor (Chromium) não está instalado, então o login e a assinatura dos uploads não vão funcionar. No Docker ele já vem na imagem; fora dele rode <code>python -m playwright install chromium</code>.</span></p>
      )}
      {d?.available && (
        <>
          <div className="divide-y rounded-xl border">
            {d.accounts.length === 0 && <p className="p-4 text-sm text-muted">Nenhuma conta conectada.</p>}
            {d.accounts.map((a) => (
              <div key={a.name} className="flex flex-wrap items-center justify-between gap-2 p-3">
                <div className="min-w-0">
                  <div className="truncate font-medium">{a.name}</div>
                  <div className="text-xs text-muted">{a.proxy ? `Proxy ${a.proxy}` : "Sem proxy"}</div>
                </div>
                <div className="flex items-center gap-2">
                  {checks[a.name] === true && <Badge tone="ok">Sessão válida</Badge>}
                  {checks[a.name] === false && <Badge tone="bad">Sessão expirada</Badge>}
                  {!(a.name in checks) && <Badge tone={a.connected ? "info" : "bad"}>{a.connected ? "Conectada" : "Sem sessão"}</Badge>}
                  <button className="btn-secondary !py-1.5" disabled={checks[a.name] === "busy"} onClick={() => check(a.name)}>
                    {checks[a.name] === "busy" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}Verificar</button>
                  <button className="btn-ghost !p-1.5" aria-label={`Remover ${a.name}`} onClick={() => confirm(`Remover a conta "${a.name}"? A sessão salva será apagada.`) && remove.mutate(a.name)}><Trash2 className="h-4 w-4" /></button>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <button className={clsx("btn-primary")} onClick={() => setConnecting(true)}><Plug className="h-4 w-4" />Conectar TikTok</button>
          </div>
          <BaseTags />
          <div className="mt-4">
            <Collapsible title="Importar sessão manualmente">
              <p className="mb-3 text-xs text-muted">Copie os cookies <code>sessionid</code> e <code>tt-target-idc</code> do tiktok.com (DevTools → Application → Cookies). O <code>sessionid</code> dá acesso total à conta: nunca compartilhe.</p>
              <div className="grid gap-3 sm:grid-cols-3">
                <Field label="Nome"><input className="input" value={imp.name} onChange={(e) => setImp({ ...imp, name: e.target.value })} /></Field>
                <Field label="sessionid"><input type="password" autoComplete="off" className="input font-mono" value={imp.sessionid} onChange={(e) => setImp({ ...imp, sessionid: e.target.value })} /></Field>
                <Field label="tt-target-idc"><input className="input font-mono" value={imp.datacenter} onChange={(e) => setImp({ ...imp, datacenter: e.target.value })} placeholder="useast2a" /></Field>
              </div>
              <button className="btn-secondary mt-3" disabled={!imp.name.trim() || !imp.sessionid.trim() || importSession.isPending} onClick={() => importSession.mutate()}>Importar</button>
            </Collapsible>
          </div>
        </>
      )}
      {connecting && <ConnectModal onClose={() => { setConnecting(false); refresh(); }} />}
    </section>
  );
}
