"""Orquestra as etapas: transcrever -> selecionar -> renderizar -> revisar. Cada etapa usa cache."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

from . import analyze, cache, judge as judgelib, media, render, review, shots as shotlib, visual
from .config import Config
from .gemini import GenaiBackend, GeminiClient, GeminiError
from .ingest import apply_meta, resolve_input
from .transcribe import Transcriber, normalize_words, split_sentences, transcribe_words

log = logging.getLogger(__name__)

WORDS_VERSION = 1


def make_client(cfg: Config, model: str | None = None) -> GeminiClient:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise GeminiError("GEMINI_API_KEY não definida (crie o .env a partir do .env.example)")
    return GeminiClient(
        GenaiBackend(key, model or cfg.gemini_model, cfg.thinking_level),
        usage_path=cfg.workspace_dir / "usage.json",
        daily_budget=cfg.daily_request_budget,
        retries=cfg.gemini_retries,
        backoff_s=cfg.gemini_backoff_s,
    )


def _setup_logging(ws: Path | None = None) -> None:
    """Console sempre; run.log na pasta do vídeo quando `ws` é conhecido."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        if getattr(h, "_darkcnn", False):
            root.removeHandler(h)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if ws is not None:
        ws.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(ws / "run.log", encoding="utf-8"))
    for h in handlers:
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        h._darkcnn = True  # type: ignore[attr-defined]
        root.addHandler(h)
    for noisy in ("httpx", "httpcore", "huggingface_hub", "google_genai", "urllib3", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def step_words(video: Path, vid: str, ws: Path, cfg: Config, transcriber: Transcriber, force: bool) -> list[dict]:
    path = ws / "words.json"
    key = cache.key_of(v=WORDS_VERSION, vid=vid, model=cfg.whisper_model, beam=cfg.whisper_beam,
                       lang=cfg.language, compute=cfg.whisper_compute_type, min_word=cfg.min_word_s)
    if not force:
        hit = cache.load(path, key)
        if hit is not None:
            log.info("transcrição em cache (%d palavras)", len(hit))
            return hit
    wav = ws / "audio.wav"
    log.info("extraindo áudio…")
    media.extract_wav(video, wav)
    log.info("transcrevendo (pode demorar: ~16 min por hora de vídeo no i7-10510U)…")
    words = normalize_words(transcriber(wav, cfg), cfg.min_word_s)
    if not words:
        raise RuntimeError("nenhuma palavra reconhecida (vídeo sem fala? para esse caso use o perfil visual, Fase 2)")
    cache.save(path, key, words)
    return words


def step_select(ws: Path, sentences: list[dict], cfg: Config, client_factory: Callable[[], GeminiClient],
                force: bool) -> list[dict]:
    path = ws / "analysis.json"
    key = cache.key_of(v=2, transcript=analyze.transcript_hash(sentences), model=cfg.gemini_model,
                       prompt=cfg.prompt_version, n=analyze.n_candidates(cfg), min=cfg.min_clip_s,
                       max=cfg.max_clip_s, thinking=cfg.thinking_level)
    if not force:
        hit = cache.load(path, key)
        if hit is not None:
            log.info("análise em cache (%d candidatos), sem chamar o Gemini", len(hit))
            return hit
    log.info("pedindo ao Gemini (%s) %d candidatos…", cfg.gemini_model, analyze.n_candidates(cfg))
    sel = client_factory().generate_json(analyze.build_prompt(sentences, cfg), analyze.TalkSelection)
    cands = [c.model_dump() for c in sel.candidates]
    cache.save(path, key, cands)
    return cands


def step_render(video: Path, vid: str, ws: Path, out: Path, clips: list[dict], words: list[dict],
                cfg: Config) -> None:
    keep: set[str] = set()
    for c in clips:
        name = f"{c['rank']:02d}_{media.slugify(c['title'])}.mp4"
        c["file"] = name
        keep.add(name)
        key = render.render_key(vid, c, words, cfg)
        log.info("corte %d/%d: %s (%.0fs)", c["rank"], len(clips), name, c["duration"])
        if not render.render_clip(video, c, words, cfg, out / name, ws / "render" / f"{c['rank']:02d}", key):
            log.info("  já renderizado (mesma configuração), pulando")
    for f in out.glob("*.mp4"):  # tira cortes de execuções anteriores que saíram da seleção
        if re.match(r"^\d\d_.*\.mp4$", f.name) and f.name not in keep:
            f.unlink()


def _paths(video: Path, cfg: Config) -> tuple[str, Path, Path]:
    vid = media.file_id(video)
    return vid, cfg.workspace_dir / vid, cfg.output_dir / vid


SHOTS_VERSION = 1


def effective_cfg(cfg: Config, meta: dict) -> Config:
    """Procedência do link + padrões do perfil: no perfil visual o texto padrão é título+gancho (não há fala)."""
    cfg = apply_meta(cfg, meta)
    if "text_mode" not in cfg.model_fields_set:  # o usuário não escolheu: padrão por perfil
        cfg = cfg.model_copy(update={"text_mode": "titled" if cfg.profile == "visual" else "both"})
    return cfg


def finalize(kept: list[dict], dropped: list[dict], cfg: Config) -> tuple[list[dict], list[dict]]:
    """Fica com os N melhores (a ordem de `kept` já é a final), numera 1..N e manda o resto para os descartados."""
    keep, extra = kept[:cfg.clips_per_video], kept[cfg.clips_per_video:]
    for rank, c in enumerate(keep, 1):
        c["rank"] = rank
    for c in extra:
        c.pop("rank", None)
    return keep, dropped + [{**c, "reason_rejected": "acima da quantidade pedida"} for c in extra]


def select_final(cands: list[dict], units: list[dict], video: Path, vid: str, ws: Path, cfg: Config,
                 duration: float, judge_factory: Callable[[], GeminiClient], theme: str | None = None,
                 force: bool = False, talk: bool = False) -> tuple[list[dict], list[dict]]:
    """candidatos da 1ª passada -> tempos exatos -> (juiz) -> N finais + descartados."""
    pool_cfg = cfg.model_copy(update={"clips_per_video": analyze.pool_size(cfg)})
    clips, rejected = analyze.resolve(cands, units, pool_cfg, duration)
    if talk:
        for c in clips:
            c["transcript"] = " ".join(units[i]["text"] for i in range(c["start_id"], c["end_id"] + 1))
    log.info("1ª passada: %d candidatos válidos, %d descartados", len(clips), len(rejected))
    if cfg.judge and len(clips) > 1:
        clips, dropped, st = judgelib.run_judge(video, vid, ws, clips, cfg, judge_factory, theme, force)
        rejected += dropped
        if st.get("judged"):
            log.info("juiz: %d mantidos, %d descartados%s", len(clips), len(dropped),
                     "; ordem MUDOU em relação à 1ª passada" if st.get("reordered") else "; mesma ordem da 1ª passada")
    return finalize(clips, rejected, cfg)


def step_shots(video: Path, vid: str, ws: Path, cfg: Config, force: bool = False) -> list[dict]:
    """Planos (cortes de edição) + volume por plano, em cache."""
    path = ws / "shots.json"
    key = cache.key_of(v=SHOTS_VERSION, vid=vid, thr=cfg.scene_threshold, min=cfg.min_shot_s, max=cfg.max_shot_s)
    if not force:
        hit = cache.load(path, key)
        if hit is not None:
            log.info("planos em cache (%d)", len(hit))
            return hit
    duration = media.probe_duration(video)
    log.info("detectando cortes de cena (limiar %.2f)… pode levar alguns minutos", cfg.scene_threshold)
    sh = shotlib.build_shots(shotlib.detect_cuts(video, cfg.scene_threshold), duration,
                             cfg.min_shot_s, cfg.max_shot_s)
    if shotlib.has_audio(video):
        log.info("medindo o volume de cada plano…")
        shotlib.annotate_loudness(sh, shotlib.loudness_series(video))
    else:
        log.info("vídeo sem áudio: sem medição de volume")
    log.info("%d planos (duração mediana %.1fs)", len(sh), sorted(x["end"] - x["start"] for x in sh)[len(sh) // 2])
    cache.save(path, key, sh)
    return sh


def shots_report(input_arg: str | Path, cfg: Config, ydl_cls: Any = None) -> str:
    """Diagnóstico sem gastar o Gemini: quantos planos, tamanho típico e os mais barulhentos."""
    _setup_logging()
    ing = resolve_input(str(input_arg), cfg, ydl_cls)
    vid, ws, _ = _paths(ing.path, cfg)
    _setup_logging(ws)
    sh = step_shots(ing.path, vid, ws, cfg)
    lens = sorted(x["end"] - x["start"] for x in sh)
    lines = [f"{len(sh)} planos; duração mediana {lens[len(lens) // 2]:.1f}s, menor {lens[0]:.1f}s, maior {lens[-1]:.1f}s",
             f"limiar de cena {cfg.scene_threshold} (use --scene-threshold: menor = mais cortes, maior = menos)"]
    loud = sorted((x for x in sh if x.get("loud") is not None), key=lambda x: -x["loud"])[:5]
    if loud:
        lines.append("planos mais barulhentos: " + ", ".join(
            f"#{x['id']} em {analyze.fmt_ts(x['start'])} ({x['loud']:+.0f}dB)" for x in loud))
    return "\n".join(lines)


def run_pipeline(input_arg: str | Path, cfg: Config, *, transcriber: Transcriber = transcribe_words,
                 client_factory: Callable[[], GeminiClient] | None = None,
                 judge_factory: Callable[[], GeminiClient] | None = None, force: bool = False,
                 ydl_cls: Any = None) -> Path:
    _setup_logging()
    ing = resolve_input(str(input_arg), cfg, ydl_cls)  # arquivo local ou download do link
    video, meta = ing.path, ing.meta
    cfg = effective_cfg(cfg, meta)
    vid, ws, out = _paths(video, cfg)
    _setup_logging(ws)
    info = media.video_info(video)
    log.info("vídeo %s (%.1f min) -> id %s", video.name, info["duration"] / 60, vid)
    log.info("config: perfil=%s layout=%s texto=%s modelo=%s cortes=%d (%.0f-%.0fs)", cfg.profile, cfg.layout,
             cfg.text_mode, cfg.gemini_model, cfg.clips_per_video, cfg.min_clip_s, cfg.max_clip_s)
    factory = client_factory or (lambda: make_client(cfg))
    jfactory = judge_factory or (client_factory or (lambda: make_client(cfg, cfg.judge_model)))

    if cfg.profile == "visual":  # sem fala: planos + vídeo reduzido para o Gemini
        words: list[dict] = []
        units = step_shots(video, vid, ws, cfg, force)
        cands, vstats = visual.select_visual(video, vid, ws, units, cfg, factory, force)
        log.info("Gemini: %d chamada(s) novas, %d ID(s) corrigido(s)", vstats["api_calls"], vstats["reconciled"])
        clips, rejected = select_final(cands, units, video, vid, ws, cfg, info["duration"], jfactory, force=force)
    else:
        if info["duration"] / 60 > cfg.max_single_window_min:
            log.warning("vídeo > %.0f min: a análise em janelas só chega na Fase 3; a qualidade pode cair",
                        cfg.max_single_window_min)
        words = step_words(video, vid, ws, cfg, transcriber, force)
        units = split_sentences(words)
        log.info("%d frases", len(units))
        cands = step_select(ws, units, cfg, factory, force)
        bad = analyze.verify_hooks(cands, units)
        if bad:
            log.warning("%d candidato(s) citaram um gancho que não está na transcrição (nota reduzida)", bad)
        clips, rejected = select_final(cands, units, video, vid, ws, cfg, info["duration"], jfactory,
                                       force=force, talk=True)

    log.info("%d cortes finais, %d descartados", len(clips), len(rejected))
    if not clips:
        log.warning("nenhum corte válido; veja rejected.json (ajuste min/max ou rode com --force)")
    out.mkdir(parents=True, exist_ok=True)
    step_render(video, vid, ws, out, clips, words, cfg)
    return review.write_review(out, video, cfg, clips, rejected, info, meta)


def render_from_selection(input_arg: str | Path, cfg: Config, ydl_cls: Any = None) -> Path:
    """Re-renderiza a partir do selection.json editado (início/fim/título/gancho), sem Whisper nem Gemini."""
    _setup_logging()
    if cfg.theme:
        cfg = compile_defaults(cfg)
    ing = resolve_input(str(input_arg), cfg, ydl_cls)
    video, meta = ing.path, ing.meta
    cfg = effective_cfg(cfg, meta)
    vid, ws, out = _paths(video, cfg)
    _setup_logging(ws)
    sel = out / "selection.json"
    if cfg.theme:  # compilado: remonta a partir do selection.json editado (ordem = campo `rank`)
        if not sel.exists():
            raise FileNotFoundError("rode `compile` antes: falta o selection.json")
        clips = json.loads(sel.read_text(encoding="utf-8"))
        for c in clips:
            c["duration"] = round(c["end"] - c["start"], 3)
        rejected = json.loads((out / "rejected.json").read_text(encoding="utf-8")) if (out / "rejected.json").exists() else []
        comp = assemble(video, vid, ws, out, clips, cfg)
        return review.write_review(out, video, cfg, clips, rejected, media.video_info(video), meta, comp)
    words = [] if cfg.profile == "visual" else cache.load(ws / "words.json", cache.key_of(v=WORDS_VERSION, vid=vid, model=cfg.whisper_model,
                       beam=cfg.whisper_beam, lang=cfg.language, compute=cfg.whisper_compute_type,
                       min_word=cfg.min_word_s))
    if not sel.exists() or words is None:
        raise FileNotFoundError("rode `run` antes: faltam selection.json ou a transcrição em cache")
    clips = json.loads(sel.read_text(encoding="utf-8"))
    for c in clips:
        c["duration"] = round(c["end"] - c["start"], 3)
    rejected = json.loads((out / "rejected.json").read_text(encoding="utf-8")) if (out / "rejected.json").exists() else []
    step_render(video, vid, ws, out, clips, words, cfg)
    return review.write_review(out, video, cfg, clips, rejected, media.video_info(video), meta)


# ------------------------------------------------------------------ compilado "Top N"
COMPILE_MIN_S, COMPILE_MAX_S = 8.0, 25.0  # trechos curtos: o compilado soma vários


def compile_defaults(cfg: Config) -> Config:
    """Padrões do compilado quando o usuário não definiu: perfil visual, texto `ranked`, trechos de 8-25 s."""
    upd: dict = {"profile": "visual"}
    if "text_mode" not in cfg.model_fields_set:
        upd["text_mode"] = "ranked"
    if "min_clip_s" not in cfg.model_fields_set:
        upd["min_clip_s"] = COMPILE_MIN_S
    if "max_clip_s" not in cfg.model_fields_set:
        upd["max_clip_s"] = max(COMPILE_MAX_S, cfg.min_clip_s) if "min_clip_s" in cfg.model_fields_set else COMPILE_MAX_S
    return cfg.model_copy(update=upd)


def prepare_theme(cands: list[dict], min_fit: int) -> tuple[list[dict], list[dict]]:
    """Descarta o que não combina com o tema e pesa o encaixe (2/3) na nota da 1ª passada."""
    ok, rej = [], []
    for c in cands:
        if c["fit"] < min_fit:
            rej.append({**c, "reason_rejected": f"não combina bem com o tema (encaixe {c['fit']}/10)"})
        else:
            ok.append({**c, "score": max(1, min(10, round((c["score"] + 2 * c["fit"]) / 3)))})
    return ok, rej


def assemble(video: Path, vid: str, ws: Path, out: Path, clips: list[dict], cfg: Config) -> dict:
    """Renderiza cada trecho (mesmo comando único de sempre, texto `ranked`) e junta em contagem regressiva:
    do pior (#K) ao melhor (#1), que fica por último. Devolve info do compilado."""
    ordered = sorted(clips, key=lambda c: -c["rank"])
    parts: list[Path] = []
    t = 0.0
    for i, c in enumerate(ordered, 1):
        c.update(rank_pos=c["rank"], theme=cfg.theme, order=i)
        seg = ws / "segments" / f"{i:02d}.mp4"
        log.info("trecho %d/%d: #%d %s (%.0fs)", i, len(ordered), c["rank"], c["title"], c["duration"])
        if not render.render_clip(video, c, [], cfg, seg, ws / "render" / f"{i:02d}", render.render_key(vid, c, [], cfg)):
            log.info("  já renderizado (mesma configuração), pulando")
        c["at"] = round(t, 2)
        t += media.probe_duration(seg)
        parts.append(seg)
    for old in (ws / "segments").glob("*.mp4"):  # segmentos de uma versão anterior com mais trechos
        if int(old.stem) > len(ordered):
            old.unlink()
    dest = out / f"compilado_{media.slugify(cfg.theme or 'top', 50)}.mp4"
    out.mkdir(parents=True, exist_ok=True)
    media.concat_copy(parts, dest, reencode_audio=True)
    duration = media.probe_duration(dest)
    for c in clips:
        c["file"] = dest.name
    log.info("compilado pronto: %s (%.0fs)", dest.name, duration)
    return {"file": dest.name, "duration": duration, "theme": cfg.theme, "requested": cfg.clips_per_video}


def run_compile(input_arg: str | Path, cfg: Config, *, client_factory: Callable[[], GeminiClient] | None = None,
                judge_factory: Callable[[], GeminiClient] | None = None, force: bool = False,
                ydl_cls: Any = None) -> Path:
    """Um vídeo-fonte -> um compilado "Top N" sobre o tema, em contagem regressiva."""
    if not cfg.theme:
        raise ValueError('informe o tema: --theme "top 5 finalizações"')
    cfg = compile_defaults(cfg)
    _setup_logging()
    ing = resolve_input(str(input_arg), cfg, ydl_cls)
    video, meta = ing.path, ing.meta
    cfg = effective_cfg(cfg, meta)
    vid, ws, out = _paths(video, cfg)
    _setup_logging(ws)
    info = media.video_info(video)
    log.info("compilado \"%s\": %s (%.1f min) -> id %s", cfg.theme, video.name, info["duration"] / 60, vid)
    log.info("config: top %d, trechos de %.0f-%.0fs, layout=%s modelo=%s juiz=%s", cfg.clips_per_video,
             cfg.min_clip_s, cfg.max_clip_s, cfg.layout, cfg.gemini_model, "sim" if cfg.judge else "não")
    factory = client_factory or (lambda: make_client(cfg))
    jfactory = judge_factory or (client_factory or (lambda: make_client(cfg, cfg.judge_model)))

    units = step_shots(video, vid, ws, cfg, force)
    cands, vstats = visual.select_visual(video, vid, ws, units, cfg, factory, force, theme=cfg.theme)
    log.info("Gemini: %d chamada(s) novas, %d ID(s) corrigido(s)", vstats["api_calls"], vstats["reconciled"])
    cands, pre_rejected = prepare_theme(cands, cfg.theme_min_fit)
    clips, rejected = select_final(cands, units, video, vid, ws, cfg, info["duration"], jfactory,
                                   theme=cfg.theme, force=force)
    rejected = pre_rejected + rejected
    if not clips:
        raise RuntimeError(f'nenhum momento combina com "{cfg.theme}" neste vídeo; veja rejected.json')
    if len(clips) < cfg.clips_per_video:
        log.warning('só achei %d momento(s) que combinam com "%s" (pedido: %d)', len(clips), cfg.theme,
                    cfg.clips_per_video)
    comp = assemble(video, vid, ws, out, clips, cfg)
    return review.write_review(out, video, cfg, clips, rejected, info, meta, comp)
