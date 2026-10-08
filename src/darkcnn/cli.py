"""Linha de comando: `python -m darkcnn run video.mp4` (ou `darkcnn run ...` após `pip install -e .`)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config, load_dotenv
from .ingest import is_url


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="darkcnn", description="Cortes verticais 9:16 a partir de um vídeo longo.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("input", metavar="VIDEO_OU_LINK", help="arquivo de vídeo local OU link http(s) (baixado com yt-dlp)")
        sp.add_argument("--config", type=Path, help="config.yaml (padrão: ./config.yaml se existir)")
        sp.add_argument("--text-mode", dest="text_mode", choices=["captions", "titled", "both", "none"],
                        help="captions = legenda por palavra; titled = título + gancho; both = contexto no topo + legenda (padrão do talk)")
        sp.add_argument("--profile", choices=["talk", "visual"],
                        help="talk = vídeo com fala (padrão); visual = sem fala (nocautes, satisfatórios)")
        sp.add_argument("--scene-threshold", dest="scene_threshold", type=float,
                        help="sensibilidade do corte de cena, 0-1 (padrão 0.30; menor = mais cortes)")
        sp.add_argument("--layout", choices=["crop", "blur"], help="crop central ou vídeo inteiro sobre fundo desfocado")
        sp.add_argument("--watermark", dest="watermark_path", type=Path, help="PNG com alpha")
        sp.add_argument("--source", help="rótulo da fonte para o review.md (NÃO baixa nada; para baixar um link, passe-o no lugar do arquivo)")
        sp.add_argument("--license", help="licença ou permissão (vai para o review.md)")
        sp.add_argument("--preset", help="preset do x264 (medium, veryfast…)")
        sp.add_argument("--whisper-model", dest="whisper_model", help="tiny/base/small/medium…")
        sp.add_argument("--workspace", dest="workspace_dir", type=Path)
        sp.add_argument("--output", dest="output_dir", type=Path)

    r = sub.add_parser("run", help="transcreve, escolhe e renderiza os cortes")
    common(r)
    r.add_argument("--model", dest="gemini_model", help="ID do modelo Gemini (veja `models.list`)")
    r.add_argument("--clips", dest="clips_per_video", type=int, help="quantidade de cortes")
    r.add_argument("--min", dest="min_clip_s", type=float, help="duração mínima (s)")
    r.add_argument("--max", dest="max_clip_s", type=float, help="duração máxima (s)")
    r.add_argument("--thinking-level", dest="thinking_level", choices=["off", "low", "medium", "high"],
                   help="raciocínio do Gemini na seleção (padrão medium; mais alto = melhor e mais lento)")
    r.add_argument("--judge-model", dest="judge_model", help="modelo do juiz (padrão: o mesmo de --model)")
    r.add_argument("--no-judge", dest="judge", action="store_const", const=False,
                   help="pula a 2ª passada (juiz) que compara os candidatos assistindo a eles")
    r.add_argument("--force", action="store_true", help="ignora o cache de transcrição e de análise")

    cp = sub.add_parser("compile", help='monta UM compilado "Top N" (contagem regressiva) a partir de um vídeo')
    common(cp)
    cp.add_argument("--theme", required=True, help='tema do compilado, ex.: "top 5 finalizações"')
    cp.add_argument("--clips", dest="clips_per_video", type=int, help="quantos momentos no top (padrão 5)")
    cp.add_argument("--min", dest="min_clip_s", type=float, help="duração mínima de cada trecho (padrão 8 s)")
    cp.add_argument("--max", dest="max_clip_s", type=float, help="duração máxima de cada trecho (padrão 25 s)")
    cp.add_argument("--model", dest="gemini_model", help="ID do modelo Gemini")
    cp.add_argument("--thinking-level", dest="thinking_level", choices=["off", "low", "medium", "high"])
    cp.add_argument("--judge-model", dest="judge_model")
    cp.add_argument("--no-judge", dest="judge", action="store_const", const=False)
    cp.add_argument("--force", action="store_true", help="ignora o cache de análise")

    nr = sub.add_parser("narrate", help="gera vídeos narrados por IA sobre uma gameplay de fundo")
    nr.add_argument("--config", type=Path)
    nr.add_argument("--format", dest="narrate_format",
                    help="curiosidade | voce-prefere | e-se | ou descreva o formato que quiser")
    nr.add_argument("--topic", help="tema (sem isso, a IA escolhe)")
    nr.add_argument("--count", dest="count", type=int, help="quantos vídeos gerar (padrão 1)")
    nr.add_argument("--target-s", dest="target_s", type=float, help="duração alvo da narração (padrão 45)")
    nr.add_argument("--voice", dest="tts_voice", help="voz do Gemini (ex.: Kore, Puck, Charon); `darkcnn voices` lista")
    nr.add_argument("--gameplay-dir", dest="gameplay_dir", type=Path, help="pasta com os vídeos de fundo")
    nr.add_argument("--game-volume", dest="game_volume", type=float, help="volume da gameplay (0 = mudo)")
    nr.add_argument("--seed", type=int, help="fixa o sorteio da gameplay")
    nr.add_argument("--layout", choices=["crop", "blur"])
    nr.add_argument("--text-mode", dest="text_mode", choices=["captions", "none"])
    nr.add_argument("--watermark", dest="watermark_path", type=Path)
    nr.add_argument("--model", dest="gemini_model")
    nr.add_argument("--thinking-level", dest="thinking_level", choices=["off", "low", "medium", "high"])
    nr.add_argument("--preset")
    nr.add_argument("--workspace", dest="workspace_dir", type=Path)
    nr.add_argument("--output", dest="output_dir", type=Path)
    nr.add_argument("--force", action="store_true", help="ignora o cache de roteiro")

    vc = sub.add_parser("voices", help="vozes do Gemini: ouve uma frase de teste e lista os modelos TTS")
    vc.add_argument("--config", type=Path)
    vc.add_argument("--say", help="frase de teste para sintetizar")
    vc.add_argument("--voice", dest="tts_voice", help="qual voz testar")
    vc.add_argument("--all", action="store_true", help="com --say: uma amostra de cada voz do pool (ou de 4 vozes)")
    vc.add_argument("--models", action="store_true", help="lista os modelos TTS que a sua chave enxerga")
    vc.add_argument("--output", dest="output_dir", type=Path)
    vc.add_argument("--workspace", dest="workspace_dir", type=Path)

    wb = sub.add_parser("web", help="abre o painel no navegador (configura e gera pela tela)")
    wb.add_argument("--config", type=Path)
    wb.add_argument("--host", default="127.0.0.1", help="padrão 127.0.0.1 (só esta máquina)")
    wb.add_argument("--port", type=int, default=8765)
    wb.add_argument("--no-browser", dest="open_browser", action="store_false",
                    help="não abre o navegador sozinho")
    wb.add_argument("--workspace", dest="workspace_dir", type=Path)
    wb.add_argument("--output", dest="output_dir", type=Path)
    wb.add_argument("--no-worker", dest="embedded_worker", action="store_false",
                    help="só enfileira; a execução fica por conta de `darkcnn worker` (outro processo)")

    wk = sub.add_parser("worker", help="executa a fila de jobs do painel (usado no Docker)")
    wk.add_argument("--config", type=Path)
    wk.add_argument("--concurrency", type=int, help="jobs ao mesmo tempo (padrão 1)")

    ml = sub.add_parser("migrate-legacy", help="importa pastas/config/histórico do uso antigo para o novo layout")
    ml.add_argument("--from", dest="origin", type=Path, default=Path("."), help="pasta do projeto antigo (padrão: .)")
    ml.add_argument("--dry-run", action="store_true", help="só mostra o que seria copiado")
    ml.add_argument("--with-workspace", action="store_true", help="copia também o cache (transcrições, downloads)")
    ml.add_argument("--config", type=Path)

    sh = sub.add_parser("shots", help="diagnóstico do perfil visual: planos e volume (não usa o Gemini)")
    common(sh)

    rr = sub.add_parser("render", help="re-renderiza a partir do selection.json editado (sem Whisper/Gemini)")
    common(rr)
    rr.add_argument("--theme", help="se o selection.json for de um compilado, o tema dele (remonta o compilado)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()
    overrides = {k: v for k, v in vars(args).items()
                 if k not in ("cmd", "input", "config", "force", "say", "all", "models",
                               "host", "port", "open_browser", "embedded_worker", "concurrency", "origin", "dry_run",
                               "with_workspace")}
    if args.cmd == "web":  # o painel lê o config.yaml a cada pedido; aqui só validamos as flags
        from .web.api import serve
        try:
            serve(args.config, args.host, args.port, overrides, args.open_browser, args.embedded_worker)
        except ImportError as e:
            print(f"\nERRO: o painel precisa das dependências web: pip install -e \".[web]\" ({e})",
                  file=sys.stderr)
            return 1
        return 0
    if args.cmd == "migrate-legacy":
        from . import storage
        from .db import Database
        from .legacy import migrate
        cfg = load_config(args.config, {})
        db = Database(storage.default_database_url(cfg.workspace_dir))
        db.init()
        rep = migrate(args.origin, cfg, db, dry_run=args.dry_run, with_workspace=args.with_workspace)
        head = "[simulação: nada foi copiado]" if args.dry_run else "Migração concluída:"
        print("\n" + head + "\n" + "\n".join(rep.lines()))
        return 0
    if args.cmd == "worker":
        from .web.api import run_worker
        return run_worker(args.config, args.concurrency)
    cfg = load_config(args.config, overrides)
    if getattr(args, "input", None) and not is_url(args.input) and getattr(args, "source", None) \
            and is_url(args.source):
        print(f"AVISO: --source só rotula a fonte; o link NÃO será baixado e o arquivo local "
              f"'{args.input}' é que será processado. Para baixar o link, rode: darkcnn {args.cmd} \"{args.source}\"",
              file=sys.stderr)

    from .pipeline import render_from_selection, run_compile, run_pipeline, shots_report

    try:
        if args.cmd == "narrate":
            from .narrate import run_narrate
            review = run_narrate(cfg, force=args.force)
            print(f"\nPronto. Revise: {review}")
            return 0
        if args.cmd == "voices":
            from .voices import voices_report
            print("\n" + voices_report(cfg, args.say, all_voices=args.all, models=args.models))
            return 0
        if args.cmd == "shots":
            print("\n" + shots_report(args.input, cfg))
            return 0
        if args.cmd == "compile":
            review = run_compile(args.input, cfg, force=args.force)
        elif args.cmd == "run":
            review = run_pipeline(args.input, cfg, force=args.force)
        else:
            review = render_from_selection(args.input, cfg)
    except Exception as e:  # mensagem curta para o usuário; o traceback fica no run.log
        print(f"\nERRO: {e}", file=sys.stderr)
        return 1
    print(f"\nPronto. Revise: {review}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
