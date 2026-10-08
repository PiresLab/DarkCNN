"""Servidor local do Chatterbox Multilingual (roda no ambiente do Chatterbox, NÃO no do DarkCNN).

    pip install chatterbox-tts          # Python 3.11 recomendado pelo projeto
    python tools/chatterbox_server.py --port 9881 [--device cpu] 

POST /tts  {"text", "language_id", "audio_prompt_path", "exaggeration", "cfg_weight"}  ->  WAV
"""
from __future__ import annotations

import argparse
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9881)
    ap.add_argument("--device", default="cpu", help="cpu | cuda | mps")
    ap.add_argument("--t3-model", default="", help="só para versões novas do chatterbox-tts: v3 ou v2 (vazio = padrão da versão instalada)")
    args = ap.parse_args()

    import torchaudio as ta
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    print(f"carregando o modelo ({args.device})… a 1ª vez baixa vários GB", flush=True)
    extra = {"t3_model": args.t3_model} if args.t3_model else {}
    model = ChatterboxMultilingualTTS.from_pretrained(device=args.device, **extra)
    print(f"pronto em http://{args.host}:{args.port}", flush=True)

    class H(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                kw = {"language_id": req.get("language_id", "pt"),
                      "exaggeration": float(req.get("exaggeration", 0.5)),
                      "cfg_weight": float(req.get("cfg_weight", 0.5))}
                if req.get("audio_prompt_path"):
                    kw["audio_prompt_path"] = req["audio_prompt_path"]
                wav = model.generate(req["text"], **kw)
                buf = io.BytesIO()
                ta.save(buf, wav.cpu(), model.sr, format="wav")
                self._send(200, buf.getvalue(), "audio/wav")
            except Exception as e:  # volta o erro legível para o DarkCNN
                self._send(400, json.dumps({"message": f"{type(e).__name__}: {e}"}).encode(), "application/json")

        def log_message(self, *a) -> None:
            pass

    ThreadingHTTPServer((args.host, args.port), H).serve_forever()


if __name__ == "__main__":
    main()
