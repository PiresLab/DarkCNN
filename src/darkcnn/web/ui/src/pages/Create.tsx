import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, Sparkles } from "lucide-react";
import { Modal, PageHeader, Field, useToast } from "../components/ui";
import { ContentStep, LookStep, ModeStep } from "../components/SpecForm";
import { api } from "../lib/api";
import { MODES } from "../lib/labels";
import { defaultSpec, type Job, type PresetSpec, type Preset } from "../lib/types";
import clsx from "clsx";

const STEPS = ["Tipo", "Conteúdo", "Voz e fundo", "Revisar"];

export function summarize(spec: PresetSpec): [string, string][] {
  const rows: [string, string][] = [["Tipo", MODES[spec.mode].title]];
  if (spec.mode === "narrate") rows.push(["Formato", spec.narrate_format]);
  rows.push(["Tema", spec.auto_topic ? `A IA escolhe${spec.niche ? ` (${spec.niche})` : ""}` : ((spec.mode === "compile" ? spec.theme : spec.topic) ?? "—")]);
  if (spec.mode !== "narrate") rows.push(["Vídeo-fonte", spec.auto_source ? "A IA procura no YouTube" : (spec.source ?? "—")]);
  rows.push([spec.mode === "narrate" ? "Vídeos" : "Cortes", String(spec.count)]);
  if (spec.mode === "narrate") {
    rows.push(["Voz", spec.tts_voice ?? "Padrão"]);
    rows.push(["Gameplay", spec.gameplay_ids.length ? `${spec.gameplay_ids.length} selecionada(s)` : "Qualquer uma"]);
  }
  return rows;
}

export default function Create() {
  const [step, setStep] = useState(0);
  const [spec, setSpec] = useState<PresetSpec>(defaultSpec());
  const [saving, setSaving] = useState(false);
  const [name, setName] = useState("");
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const set = (p: Partial<PresetSpec>) => setSpec((s) => ({ ...s, ...p }));

  const run = useMutation({
    mutationFn: () => api<Job>("/runs", { method: "POST", json: { name: "", spec } }),
    onSuccess: (job) => { qc.invalidateQueries({ queryKey: ["jobs"] }); toast("Geração iniciada"); nav(`/execucoes/${job.id}`); },
    onError: (e: Error) => toast(e.message, "bad"),
  });
  const save = useMutation({
    mutationFn: () => api<Preset>("/presets", { method: "POST", json: { name, spec } }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["presets"] }); toast("Automação salva"); nav("/automacoes"); },
    onError: (e: Error) => toast(e.message, "bad"),
  });

  const aiCalls = spec.mode === "narrate" ? 1 + (spec.auto_topic ? 1 : 0) : 2 + spec.count;
  const last = step === STEPS.length - 1;
  const lookSkipped = spec.mode !== "narrate";

  return (
    <>
      <PageHeader title="Criar vídeo" subtitle="Escolha o que quer produzir. A IA cuida do resto." />
      <ol className="mb-6 flex flex-wrap gap-2" aria-label="Etapas">
        {STEPS.map((s, i) => (
          <li key={s} className={clsx("flex items-center gap-2 rounded-full px-3 py-1 text-sm", i === step ? "bg-brand/20 font-semibold text-brand" : i < step ? "text-fg" : "text-muted")}>
            <span className={clsx("grid h-5 w-5 place-items-center rounded-full text-xs font-bold", i <= step ? "bg-brand text-brand-fg" : "bg-raised")}>{i + 1}</span>{s}
          </li>
        ))}
      </ol>

      <div className="card p-5 sm:p-6">
        {step === 0 && <ModeStep spec={spec} set={(p) => setSpec((s) => ({ ...defaultSpec(p.mode), ...(p.mode ? { mode: p.mode } : {}), tts_voice: s.tts_voice }))} />}
        {step === 1 && <ContentStep spec={spec} set={set} />}
        {step === 2 && <LookStep spec={spec} set={set} />}
        {step === 3 && (
          <div className="space-y-4">
            <dl className="divide-y rounded-xl border">
              {summarize(spec).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-4 px-4 py-2.5 text-sm"><dt className="text-muted">{k}</dt><dd className="text-right font-medium">{v}</dd></div>
              ))}
            </dl>
            <p className="text-sm text-muted">Usa cerca de {aiCalls} chamada(s) da IA{spec.mode === "narrate" ? " mais a voz" : ""}.</p>
          </div>
        )}
      </div>

      <div className="mt-5 flex flex-wrap justify-between gap-3">
        <button className="btn-secondary" disabled={step === 0} onClick={() => setStep(step === 3 && lookSkipped ? 1 : step - 1)}><ArrowLeft className="h-4 w-4" />Voltar</button>
        {!last ? (
          <button className="btn-primary" onClick={() => setStep(step === 1 && lookSkipped ? 3 : step + 1)}>Continuar<ArrowRight className="h-4 w-4" /></button>
        ) : (
          <div className="flex gap-2">
            <button className="btn-secondary" onClick={() => setSaving(true)}>Salvar como automação</button>
            <button className="btn-primary" disabled={run.isPending} onClick={() => run.mutate()}><Sparkles className="h-4 w-4" />Gerar agora</button>
          </div>
        )}
      </div>

      <Modal open={saving} onClose={() => setSaving(false)} title="Salvar como automação"
        footer={<><button className="btn-secondary" onClick={() => setSaving(false)}>Cancelar</button>
          <button className="btn-primary" disabled={!name.trim() || save.isPending} onClick={() => save.mutate()}>Salvar</button></>}>
        <Field label="Nome" hint="Depois você agenda os horários na tela Automações.">
          <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Curiosidades de ciência" />
        </Field>
      </Modal>
    </>
  );
}
