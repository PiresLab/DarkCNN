# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

DarkCNN gera vídeos verticais 9:16 (cortes de vídeos longos, compilados "Top N" e narrações sobre gameplay) e agora tem um painel web (FastAPI + React), fila de jobs em banco, automações agendadas e postagem no TikTok. Texto de interface, logs, comentários e commits são em português (pt-BR); siga isso.

## Comandos

```bash
pip install -e ".[dev,web,tiktok]" ruff     # Python >= 3.10; precisa de ffmpeg/ffprobe no PATH
ruff check src tests                        # só regras E9 e F (pyproject.toml)
pytest                                      # suíte inteira: ~27 min no Windows (usa ffmpeg real)
pytest tests/test_web.py -q                 # um arquivo
pytest tests/test_tts.py -k "throttle"      # um teste
darkcnn web --no-browser --port 8765        # painel (worker embutido); `--no-worker` + `darkcnn worker` separa os dois
darkcnn run|compile|narrate|render ...      # CLI original; veja `darkcnn <cmd> -h`

cd src/darkcnn/web/ui && npm install && npm run build   # build da UI vai para src/darkcnn/web/static (gitignored)
npm run dev                                 # Vite na 5173, repassa /api para a 8765
docker compose up -d --build                # app + worker + postgres; http://localhost:8765
```

- Testes usam ffmpeg real em vídeo sintético; Gemini, TTS e Whisper são falsos (`tests/conftest.py`: `FakeBackend`, `needs_ffmpeg`). Os testes de navegador real do TikTok (`test_real_*`) pulam sozinhos sem Chromium/Chrome.
- Para rodar o painel local sem Docker, a UI precisa estar compilada; sem build o servidor devolve uma página avisando.
- Há `.env` (`GEMINI_API_KEY`) e `config.yaml` na raiz, ambos ignorados pelo git.

## Arquitetura

**Pipeline (`src/darkcnn/`)** — cada comando da CLI é uma função de `pipeline.py`/`narrate.py`, com cache em disco por etapa (`cache.py`, chave = hash dos parâmetros):
- `run`: ingest (`ingest.py`, yt-dlp ou arquivo) → Whisper (`transcribe.py`) → Gemini escolhe trechos **por ID de frase** (`analyze.py`; o Gemini nunca devolve tempos, os tempos vêm do Whisper) → juiz opcional (`judge.py`) → `render.py` (ffmpeg, 1 encode por corte) → `review.py`. Perfil `visual` (`shots.py`, `visual.py`) troca a transcrição por detecção de planos + vídeo reduzido enviado ao Gemini.
- `compile`: igual ao perfil visual, mas monta o Top N em contagem regressiva (`pipeline.assemble`).
- `narrate`: roteiro (`script.py`) → TTS do Gemini (`tts.py`) → Whisper alinha legenda (`align.py`) → render sobre gameplay. Legenda/contagem/opções são ASS (`textlayers.py`), queimadas pelo filtro `ass` do ffmpeg.
- `Config` (`config.py`, pydantic, `extra="forbid"`): chave desconhecida no `config.yaml` quebra o carregamento — ao remover um campo, avise que configs antigos que o usam falham. Caminhos padrão vêm de `storage.py` (`DATA_DIR` no Docker; sem ele, pastas relativas ao cwd).
- Orçamento de chamadas ao Gemini: só `daily_request_budget` (contador em `workspace/usage.json`). Não há esperas fixas de free tier; o retry com backoff em `gemini.py`/`tts.py` só reage a erro real (429/5xx).

**Painel e jobs (`web/`)**
- `web/api.py` monta o FastAPI (`create_app`, `AppState`); rotas extras ficam em `routes_auto.py` (gameplays/presets/agendas), `routes_settings.py` (modelos, marca d'água, preview de agenda) e `routes_tiktok.py`. A SPA é servida por fallback; `/api/*` desconhecido dá 404.
- Fila persistente: `web/jobs.py` (`JobStore` + `Worker`) sobre SQLAlchemy (`db/`; Postgres no Docker, SQLite em `workspace/darkcnn.db` local; tabelas criadas com `create_all`, sem Alembic). A API só enfileira; o `Worker` (embutido ou `darkcnn worker`) reivindica jobs com UPDATE condicional e roda os mesmos executores da CLI. Log por job é capturado por thread e gravado em `job_logs`. Modos de job: `run`, `compile`, `render`, `narrate`, `auto`, `post`.
- Automação: `presets.py` (`PresetSpec` → `Config` via `build_config`), `autopilot.py` (IA propõe tema e queries, yt-dlp busca, IA escolhe o vídeo), `scheduler.py` (cron local, `@every Nm|Nh`, várias expressões com `;`; roda dentro do worker). Quando um job `auto` termina, o worker enfileira jobs `post` se o preset tiver TikTok ligado.
- Preset: `count` = vídeos narrados (cada um com assunto próprio: `Config.topics`, um `propose_topic` por vídeo); `clips` = cortes (vira `clips_per_video`) ou momentos do compilado (vira `compile_clips`); são campos diferentes. O compilado usa `compile_clips/compile_min_clip_s/compile_max_clip_s` (padrão 5 × 6–12 s), nunca os campos dos cortes, e é sempre UM vídeo (`review.unique_videos` colapsa o `selection.json` na Biblioteca e nas postagens).
- Gameplays/"Backgrounds": enviados pela UI para `<gameplays>/` e cadastrados no banco (`gameplays.py`); `Config.gameplay_files` restringe o sorteio ao subconjunto do preset.

**TikTok (`tiktok/`)** — usa o `autotok` (AGPL-3.0, extra `tiktok`) como biblioteca, importado só aqui e de forma preguiçosa. Sessões ficam em `<data>/tiktok/accounts` (equivalem à senha; pasta gitignored — a regra no `.gitignore` é `/tiktok/`, não `tiktok/`, senão ignora o pacote). Login remoto: `login.py` abre o Chromium no servidor e a UI mostra os quadros e repassa cliques/teclas. `client.py` (`StudioClient`) sobrescreve o `_payload` do autotok com os campos que o TikTok Studio manda hoje; sem eles o TikTok responde `status_code 5 / Invalid parameters`. Hashtags: lista de alcance editável (`Config.tiktok_base_tags`) + 3 do assunto pelo Gemini (`captions.compose`).

**UI (`web/ui/`)** — React + Vite + TS + Tailwind; React Query com hooks em `lib/hooks.ts`, tipos em `lib/types.ts` (espelham os modelos pydantic), rótulos em português em `lib/labels.ts`. A configuração visível ao usuário tem esquema próprio em `pages/Settings.tsx` (não é gerada do `Config`).

## Pegadinhas

- Ferramentas de edição neste ambiente podem desfazer escapes (`\n`, `\r`, `\U`) em scripts gerados por heredoc; prefira editar arquivos pelas ferramentas do editor e confira o resultado.
- `/api/media` e rotas de revisão usam `safe_under()` para nunca servir fora da pasta de saída; mantenha ao adicionar rotas de arquivo.
- Mudanças em `Dockerfile`/`pyproject.toml` exigem `docker compose up -d --build`; o contêiner não monta o código-fonte.
