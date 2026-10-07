"""Linha de comando: `python -m darkcnn run video.mp4` (ou `darkcnn run ...` após `pip install -e .`)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config, load_dotenv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="darkcnn", description="Cortes verticais 9:16 a partir de um vídeo longo.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("video", type=Path, help="arquivo de vídeo local")
        sp.add_argument("--config", type=Path, help="config.yaml (padrão: ./config.yaml se existir)")
        sp.add_argument("--text-mode", dest="text_mode", choices=["captions", "titled", "none"],
                        help="captions = legenda por palavra; titled = título no topo + gancho embaixo")
        sp.add_argument("--layout", choices=["crop", "blur"], help="crop central ou vídeo inteiro sobre fundo desfocado")
        sp.add_argument("--watermark", dest="watermark_path", type=Path, help="PNG com alpha")
        sp.add_argument("--source", help="URL/descrição da fonte (vai para o review.md)")
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
    r.add_argument("--force", action="store_true", help="ignora o cache de transcrição e de análise")

    rr = sub.add_parser("render", help="re-renderiza a partir do selection.json editado (sem Whisper/Gemini)")
    common(rr)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()
    overrides = {k: v for k, v in vars(args).items() if k not in ("cmd", "video", "config", "force")}
    cfg = load_config(args.config, overrides)

    from .pipeline import render_from_selection, run_pipeline

    try:
        if args.cmd == "run":
            review = run_pipeline(args.video, cfg, force=args.force)
        else:
            review = render_from_selection(args.video, cfg)
    except Exception as e:  # mensagem curta para o usuário; o traceback fica no run.log
        print(f"\nERRO: {e}", file=sys.stderr)
        return 1
    print(f"\nPronto. Revise: {review}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
