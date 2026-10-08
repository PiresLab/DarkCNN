import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { Badge, Empty, Field, Modal, PageHeader, Skeleton, Toggle, useToast } from "../components/ui";
import { ContentStep, LookStep, ModeStep } from "../components/SpecForm";
import { api } from "../lib/api";
import { CRON_PRESETS, MODES, cronLabel } from "../lib/labels";
import { fmtWhen } from "../lib/format";
import { usePresets } from "../lib/hooks";
import { defaultSpec, type Job, type Preset, type PresetSpec } from "../lib/types";

interface Draft { id?: string; name: string; spec: PresetSpec; cron: string; enabled: boolean; scheduled: boolean }

const blank = (): Draft => ({ name: "", spec: defaultSpec(), cron: "0 18 * * *", enabled: true, scheduled: false });

export default function Automations() {
  const presets = usePresets();
  const qc = useQueryClient();
  const nav = useNavigate();
  const toast = useToast();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [tab, setTab] = useState(0);
  const refresh = () => qc.invalidateQueries({ queryKey: ["presets"] });

  const save = useMutation({
    mutationFn: async (d: Draft) => {
      const body = { name: d.name, spec: d.spec };
      const p = d.id ? await api<Preset>(`/presets/${d.id}`, { method: "PUT", json: body })
        : await api<Preset>("/presets", { method: "POST", json: body });
      if (d.scheduled) await api(`/presets/${p.id}/schedule`, { method: "PUT", json: { cron: d.cron, enabled: d.enabled } });
      else if (d.id) await api(`/presets/${p.id}/schedule`, { method: "DELETE" });
      return p;
    },
    onSuccess: () => { refresh(); setDraft(null); toast("Automação salva"); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api(`/presets/${id}`, { method: "DELETE" }),
    onSuccess: () => { refresh(); toast("Automação removida"); },
  });
  const runNow = useMutation({
    mutationFn: (id: string) => api<Job>(`/presets/${id}/run`, { method: "POST" }),
    onSuccess: (j) => { qc.invalidateQueries({ queryKey: ["jobs"] }); nav(`/execucoes/${j.id}`); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const toggle = useMutation({
    mutationFn: (p: Preset) => api(`/presets/${p.id}/schedule`, { method: "PUT", json: { cron: p.schedule!.cron, enabled: !p.schedule!.enabled } }),
    onSuccess: refresh,
  });

  const edit = (p?: Preset) => {
    setTab(0);
    setDraft(p ? { id: p.id, name: p.name, spec: { ...defaultSpec(), ...p.spec }, cron: p.schedule?.cron ?? "0 18 * * *",
      enabled: p.schedule?.enabled ?? true, scheduled: !!p.schedule } : blank());
  };
  const set = (patch: Partial<PresetSpec>) => setDraft((d) => d && { ...d, spec: patch.mode ? { ...defaultSpec(patch.mode), tts_voice: d.spec.tts_voice } : { ...d.spec, ...patch } });

  return (
    <>
      <PageHeader title="Automações" subtitle="Receitas que rodam sozinhas: a IA escolhe o tema, acha o vídeo e produz."
        actions={<button className="btn-primary" onClick={() => edit()}><Plus className="h-4 w-4" />Nova automação</button>} />

      {presets.isLoading ? <Skeleton className="h-32" /> : (presets.data ?? []).length === 0 ? (
        <Empty icon={<CalendarClock className="h-8 w-8" />} title="Nenhuma automação ainda">
          Crie uma receita, agende os horários e deixe o canal alimentado sozinho.
        </Empty>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {presets.data!.map((p) => (
            <div key={p.id} className="card flex flex-col gap-3 p-5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="truncate font-semibold">{p.name}</h3>
                  <p className="text-sm text-muted">{MODES[p.spec.mode]?.title}{p.spec.mode === "narrate" ? ` · ${p.spec.narrate_format}` : ""}</p>
                </div>
                {p.schedule ? <Badge tone={p.schedule.enabled ? "ok" : "muted"}>{p.schedule.enabled ? "Ativa" : "Pausada"}</Badge> : <Badge>Manual</Badge>}
              </div>
              <div className="text-sm text-muted">
                {p.spec.auto_topic ? "Tema escolhido pela IA" : "Tema fixo"}{p.spec.niche ? ` · ${p.spec.niche}` : ""}
                {p.schedule && <><br />{cronLabel(p.schedule.cron)}{p.schedule.enabled && p.schedule.next_run ? ` · próxima: ${fmtWhen(p.schedule.next_run)}` : ""}</>}
              </div>
              <div className="mt-auto flex flex-wrap gap-2">
                <button className="btn-primary" disabled={runNow.isPending} onClick={() => runNow.mutate(p.id)}><Play className="h-4 w-4" />Rodar agora</button>
                <button className="btn-secondary" onClick={() => edit(p)}><Pencil className="h-4 w-4" />Editar</button>
                {p.schedule && <button className="btn-ghost" onClick={() => toggle.mutate(p)}>{p.schedule.enabled ? "Pausar" : "Retomar"}</button>}
                <button className="btn-ghost ml-auto" aria-label="Excluir" onClick={() => confirm(`Excluir "${p.name}"?`) && remove.mutate(p.id)}><Trash2 className="h-4 w-4" /></button>
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal open={!!draft} onClose={() => setDraft(null)} wide title={draft?.id ? "Editar automação" : "Nova automação"}
        footer={<><button className="btn-secondary" onClick={() => setDraft(null)}>Cancelar</button>
          <button className="btn-primary" disabled={!draft?.name.trim() || save.isPending} onClick={() => draft && save.mutate(draft)}>Salvar</button></>}>
        {draft && (
          <div className="space-y-5">
            <Field label="Nome"><input className="input" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder="Curiosidades de ciência" autoFocus /></Field>
            <div className="flex gap-1 border-b">
              {["Tipo", "Conteúdo", "Voz e fundo", "Agenda"].map((t, i) => (
                <button key={t} onClick={() => setTab(i)} className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${tab === i ? "border-brand text-brand" : "border-transparent text-muted"}`}>{t}</button>
              ))}
            </div>
            {tab === 0 && <ModeStep spec={draft.spec} set={set} />}
            {tab === 1 && <ContentStep spec={draft.spec} set={set} />}
            {tab === 2 && <LookStep spec={draft.spec} set={set} />}
            {tab === 3 && (
              <div className="space-y-4">
                <Toggle checked={draft.scheduled} onChange={(v) => setDraft({ ...draft, scheduled: v })} label="Rodar automaticamente" hint="Sem agenda, a automação só roda quando você clicar em Rodar agora." />
                {draft.scheduled && (
                  <>
                    <Field label="Frequência">
                      <select className="input" value={CRON_PRESETS.some((c) => c.cron === draft.cron) ? draft.cron : "custom"}
                        onChange={(e) => e.target.value !== "custom" && setDraft({ ...draft, cron: e.target.value })}>
                        {CRON_PRESETS.map((c) => <option key={c.cron} value={c.cron}>{c.label}</option>)}
                        <option value="custom">Personalizada…</option>
                      </select>
                    </Field>
                    {!CRON_PRESETS.some((c) => c.cron === draft.cron) && (
                      <Field label="Expressão cron" hint="5 campos: minuto hora dia mês dia-da-semana. Ex.: 30 8 * * 1-5">
                        <input className="input font-mono" value={draft.cron} onChange={(e) => setDraft({ ...draft, cron: e.target.value })} />
                      </Field>
                    )}
                    <Toggle checked={draft.enabled} onChange={(v) => setDraft({ ...draft, enabled: v })} label="Agenda ativa" />
                    <p className="text-xs text-muted">O horário segue o fuso do servidor. Se o computador estiver desligado, a execução perdida roda uma vez quando voltar.</p>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </Modal>
    </>
  );
}
