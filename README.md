# DarkCNN

Pipeline local para transformar um vídeo longo em **cortes verticais 9:16** prontos para revisar e postar
(Shorts, Reels, TikTok), com legenda ou título/gancho na tela e marca d'água. A postagem é manual.

```
vídeo -> Whisper local (palavras com tempo) -> Gemini escolhe os trechos (por ID de frase)
      -> FFmpeg corta + reenquadra + texto + marca d'água (1 encode por corte) -> pasta de revisão
```

**Princípio:** o Gemini decide *o quê* cortar; as ferramentas locais decidem *onde*. O Gemini nunca devolve
tempos (erro mediano medido de ~0,5 s): devolve IDs de frase, e os tempos vêm do Whisper. Todo corte começa e
termina em limite de frase. Detalhes e números medidos: [`docs/fase0-resultados.md`](docs/fase0-resultados.md).

## Início rápido com Docker (recomendado)

```bash
cp .env.example .env          # cole a GEMINI_API_KEY (ou salve depois, em Configurações)
docker compose up --build     # abre http://localhost:8765
```

Sobem três serviços: `app` (API + interface), `worker` (executa a fila e dispara as automações agendadas)
e `db` (Postgres). Os dados ficam em volumes: `data` (`/data/gameplays`, `/data/outputs`, `/data/workspace`,
`config.yaml`, chave do Gemini) e `pgdata`. Reiniciar ou recriar os containers não perde histórico, vídeos nem
gameplays enviadas.

> Sem login: a porta só é publicada em `127.0.0.1`. Não exponha na internet sem um proxy com autenticação.

### Trazendo o que você já tinha
```bash
darkcnn migrate-legacy --dry-run          # mostra o que seria copiado (a origem nunca é alterada)
darkcnn migrate-legacy [--with-workspace] # copia config.yaml, saídas, gameplays e importa o histórico
```
Dentro do Docker: `docker compose run --rm -v "$PWD":/old app darkcnn migrate-legacy --from /old`.

## Instalação local (Windows, PowerShell)
```powershell
winget install Gyan.FFmpeg                    # FFmpeg "full" (precisa de libass); reabra o terminal
winget install DenoLand.Deno                  # runtime JS que o yt-dlp pede para baixar do YouTube em qualidade total
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .                              # instala o darkcnn e as dependências
copy .env.example .env                        # cole sua GEMINI_API_KEY (https://aistudio.google.com/apikey)
copy config.example.yaml config.yaml          # opcional
```
Confira o ambiente: `python spikes\00_check_env.py`.

## Uso
```powershell
# link (baixa com yt-dlp; título, canal e licença do vídeo vão para o review.md)
python -m darkcnn run "https://youtu.be/XXXX"
# arquivo local (use --source/--license só para rotular a procedência)
python -m darkcnn run video.mp4 --source "https://youtu.be/XXXX" --license "CC-BY 4.0"
```
> `--source` **não baixa nada**: é só um rótulo para o `review.md`. Para baixar um link, passe o link no lugar do arquivo.
> Vídeos baixados ficam em `workspace\downloads\`; rodar de novo com o mesmo link não baixa outra vez.

Saída em `output\<video>\`: `01_titulo.mp4`, `02_…`, `review.md` (título, gancho, motivo, notas, tempo na fonte
com link `&t=` no YouTube), `selection.json` (editável) e `rejected.json` (candidatos descartados e o motivo).

| Opção | O que faz |
|---|---|
| `--text-mode both` | **contexto no topo** (uma linha que situa quem chega no meio da conversa) + legenda por palavra (padrão do `--profile talk`) |
| `--text-mode captions` | só a legenda por palavra, palavra falada em amarelo |
| `--text-mode titled` | **título no topo + frase-gancho embaixo**, sem legenda de fala (padrão do `--profile visual`) |
| `--text-mode none` | sem texto |
| `compile --theme "…"` | monta um compilado "Top N" num vídeo só (veja a seção acima) |
| `narrate --format …` | gera vídeos narrados por IA sobre gameplay (veja a seção acima) |
| `--profile talk` / `visual` | vídeo com fala (padrão) / sem fala: escolhe olhando o vídeo |
| `--layout blur` / `crop` | vídeo inteiro sobre fundo desfocado (padrão) / corte central 9:16 |
| `--watermark logo.png` | PNG com alpha (posição e opacidade no `config.yaml`) |
| `--clips 5 --min 30 --max 60` | quantidade e duração dos cortes |
| `--model ID` | modelo Gemini (veja os IDs com `python spikes\03_gemini_probe.py --list-models`) |
| `--preset veryfast` | render mais rápido, arquivo maior (padrão `medium`) |
| `--model ID` / `--thinking-level high` | modelo e nível de raciocínio da seleção (veja "Como a IA escolhe") |
| `--judge-model ID` / `--no-judge` | modelo do juiz / pula a 2ª passada |
| `--force` | ignora o cache de transcrição e de análise |

## Compilado "Top N" (um vídeo só, contagem regressiva)
Para transformar **um** vídeo-fonte (ex.: uma compilação de lutas) em **um** vídeo "top 5 finalizações", "top 5 reviravoltas":
```powershell
python -m darkcnn compile "https://youtu.be/XXXX" --theme "top 5 finalizações"
```
Sai `output\<video>\compilado_top-5-finalizacoes.mp4` (1080x1920) com os momentos em **contagem regressiva** (#5 → #1: o melhor
por último). Cada trecho mostra o tema fixo e o título no topo, um selo `#N` no canto, o número grande na entrada e a
frase-gancho embaixo. Padrões: 5 momentos de 8 a 25 s (`--clips`, `--min`, `--max`).

Como funciona: o Gemini procura só os momentos que **são exemplos fortes do tema** (dá uma nota de encaixe e descarta
os que não combinam), depois o **juiz** assiste a todos e os ranqueia comparando, e o código monta o vídeo. Se houver
menos momentos bons do que o pedido, o compilado sai com menos e o `review.md` avisa (melhor poucos e fortes).
O `review.md` lista cada trecho (posição, minuto na fonte com link, minuto no compilado, notas) e repete a licença
da fonte, que vale para todos os trechos. Passa de 3 min? Ele avisa (limite do YouTube Shorts).

**Mudar a ordem ou os limites:** edite `rank`, `start`, `end` ou `title` no `selection.json` (o `rank` define a posição) e rode
`python -m darkcnn render "<link>" --theme "top 5 finalizações"`: só os trechos alterados são renderizados de novo.

## Interface web (`web`)

```powershell
pip install -e ".[web]"
cd src\darkcnn\web\ui; npm install; npm run build; cd ..\..\..\..   # compila a interface (uma vez)
python -m darkcnn web                 # http://127.0.0.1:8765 (fila e worker embutidos, SQLite local)
python -m darkcnn web --no-worker     # só API: rode `python -m darkcnn worker` em outro terminal
```

- **Criar vídeo:** assistente em passos (tipo, conteúdo, voz e fundo). "Gerar agora" ou "Salvar como automação".
- **Automações:** presets que rodam sozinhos. A IA propõe o tema (sem repetir os anteriores) e, nos cortes e
  compilados, busca no YouTube (yt-dlp), filtra por duração e escolhe o vídeo-fonte. Só as gameplays são fixas.
  Agenda por cron (ex.: todo dia às 18:00); se o servidor ficou desligado, a execução perdida roda uma vez.
- **Meus vídeos:** prévia, aprovar/rejeitar, ajustar título e cortes, refazer e baixar.
- **Gameplays:** upload pela interface (validado com ffprobe, com miniatura) para o volume.
- **Execuções:** fila persistente, andamento por etapa, cancelamento e log técnico recolhido.
- **Configurações:** chave do Gemini, voz (com prévia), aparência e limites. O resto fica em "Avançado".

Desenvolvimento da interface: `npm run dev` em `src/darkcnn/web/ui` (porta 5173, repassa `/api` para o 8765).

Variáveis: `DATA_DIR` (raiz dos dados; no Docker `/data`), `DATABASE_URL` (Postgres; padrão SQLite em
`workspace/darkcnn.db`), `DARKCNN_WORKER_CONCURRENCY` (jobs simultâneos, padrão 1).

> Serve em `127.0.0.1` e **não tem senha**. Não exponha a porta na internet nem rode com `--host 0.0.0.0`
> numa rede que você não controla.

## Vídeos narrados sobre gameplay (`narrate`)
Sem vídeo-fonte: a IA escreve o roteiro, uma voz narra e o texto aparece como legenda sobre uma gameplay
aleatória da sua pasta.
```powershell
python -m darkcnn voices --models                      # 1) qual modelo TTS a sua chave enxerga
python -m darkcnn voices --say "testando a voz" --all  # 2) ouça algumas vozes (output\voices\*.wav)
python -m darkcnn narrate --format curiosidade --count 3 --voice Kore --gameplay-dir gameplays\
python -m darkcnn narrate --format voce-prefere --count 2
```
Sai `output\narrate\<id>\01_titulo.mp4` + `review.md` com o roteiro falado, o tema e a gameplay usada.

| Formato | O que é |
|---|---|
| `curiosidade` | um fato surpreendente e verdadeiro, explicado do choque para a causa |
| `voce-prefere` | dilemas com **duas opções na tela e contagem regressiva** para o espectador decidir |
| `e-se` | hipótese absurda levada a sério (ex.: um humano de hoje na época dos dinossauros) |
| qualquer texto | `--format "mitos desmentidos"` vira instrução direta para a IA |

Sem `--topic`, a IA escolhe o tema. Outras opções: `--count` (quantos vídeos), `--target-s` (duração alvo),
`--game-volume` (padrão 6%), `--seed` (fixa o sorteio da gameplay), `--layout`, `--watermark`, `--model`.

**Como os tempos da legenda são exatos:** a voz é sintetizada por bloco de roteiro, o Whisper ouve a narração e o
código casa o que ele ouviu com o texto **que sabemos que foi falado**. Assim a legenda mostra o roteiro
(sem erro de audição) com o tempo real de cada palavra. Se o Whisper reconhecer pouco, o `review.md` avisa.

**Cache:** roteiro e voz ficam em `workspace\narrate\<id>\`. Mudar o texto de um bloco re-sintetiza **só ele**.
Rodar o mesmo comando de novo não gasta nada.

**Tic-tac da contagem:** no `voce-prefere`, enquanto os números 3, 2, 1 aparecem, toca um tic-tac de relógio
(um tic e um tac por segundo). O som é gerado pelo código, sem arquivo de áudio de terceiros. `tick_volume` no
`config.yaml` ajusta (0 desliga).

**A abertura:** o prompt manda a 1ª frase falar com o espectador e fazer uma pergunta que ele não responde de cabeça,
segurar a resposta por pelo menos duas linhas e entregar exatamente o que prometeu (sem isca vazia). O código confere a
abertura (saudação, "você sabia que", sem pergunta/"você", mais de ~16 palavras) e, se achar fraca, marca
"⚠ Abertura fraca" no `review.md` sem barrar o vídeo. Para refazer só os roteiros: `--force`.

### A voz (TTS do Gemini)
Usa a mesma `GEMINI_API_KEY` do resto. Sem instalar nada: a voz vem do próprio Gemini (vozes prontas como `Kore`,
`Puck`, `Charon`…), em português do Brasil, com o estilo definido em `tts_style`.
- **Poucas requisições:** a voz é pedida por bloco, então um vídeo de `curiosidade`/`e-se` gasta **1 requisição de voz**;
  `voce-prefere` gasta uma por pergunta. O TTS tem limite próprio (por minuto e por dia) e `tts_min_interval_s`
  espaça as chamadas. **Não confirmei se o free tier inclui TTS** (as fontes divergem): olhe a página de limites do
  AI Studio e rode `darkcnn voices --models`.
- **ID do modelo e vozes mudam** (os modelos TTS são "preview"): `tts_model` e `tts_voice` ficam no `config.yaml`.
- **Qualidade:** ouça as vozes com `voices --say ... --all`, ajuste `tts_style` (tom, ritmo) e `tts_speed` (1.1 deixa
  mais "de short"). `tts_voices_pool: [Kore, Puck, Charon]` sorteia uma voz por vídeo.

> **Antes de publicar:** narração de IA sobre gameplay feita em série é o caso que as plataformas tratam como
> conteúdo em massa, e a voz é sintética (marque como conteúdo alterado/sintético onde a plataforma pedir).
> O `review.md` repete os avisos, e o roteiro é escrito por IA: confira os fatos antes de postar.

## Como a IA escolhe os cortes (e como melhorar)
A escolha tem **duas passadas**:
1. **Seleção:** o Gemini lê a transcrição (fala) ou assiste ao vídeo em janelas (sem fala) e propõe ~3× mais candidatos
   do que você pediu, cada um com título, contexto, gancho e notas. No perfil de fala ele também copia a frase do gancho,
   e o código confere se ela existe mesmo na transcrição (se não, a nota cai: sinal de alucinação).
2. **Juiz:** o código corta os candidatos nos tempos exatos, junta todos num vídeo único (rótulo A1, A2… no canto) e o
   Gemini **assiste e ouve** cada um, **comparando-os entre si** (nota 1-100 sem empates, ponto forte e fraco, e veta os
   que não publicaria). O ranking final é o do juiz. Se o juiz falhar, vale a ordem da 1ª passada.

O `review.md` mostra a nota da 1ª passada, a do juiz e o motivo, para você ver onde a IA mudou de ideia e julgar quem acerta mais.

Alavancas, da que mais pesa para a que menos pesa:
- **Modelo:** `--model gemini-3.5-flash` (ou 3.6/3.7/3.8) costuma julgar melhor que o `flash-lite`; veja os IDs com
  `python spikes\03_gemini_probe.py --list-models`. Modelos maiores podem ter limite menor no free tier: se aparecer erro 429,
  volte ao anterior ou espere.
- **Raciocínio:** `--thinking-level high` deixa o modelo "pensar" mais antes de responder (mais lento, melhor). Se o modelo
  não aceitar, o programa desliga sozinho e avisa.
- **`--no-judge`** para comparar com e sem a 2ª passada no mesmo vídeo (a transcrição e a 1ª passada ficam em cache).

Isso melhora as chances, não garante: o gosto final é seu. Rode 2 ou 3 vídeos e veja se os cortes que você postaria
estão entre os primeiros.

## Vídeos sem fala (nocautes, vídeos satisfatórios): `--profile visual`
Sem fala não há transcrição, então o programa escolhe os momentos olhando o vídeo:
```powershell
python -m darkcnn shots "https://youtu.be/XXXX"      # 1) diagnóstico, não usa o Gemini: quantos cortes de cena?
python -m darkcnn run "https://youtu.be/XXXX" --profile visual --clips 3 --min 15 --max 40
```
O texto na tela vira **título no topo + frase-gancho embaixo** (`--text-mode titled`, o padrão desse perfil).

Como funciona: o código detecta os cortes de edição (planos) e mede o volume de cada um (reação da plateia,
impacto). Para cada janela de ~10 min ele monta um vídeo pequeno (360p) com o **número do plano gravado no canto**
e pede ao Gemini os melhores momentos **por número de plano**: o corte começa e termina sempre num corte de
edição, nunca no meio de um golpe. Se o Gemini errar o número, o código confere com o tempo que ele informou e
corrige. Cada janela fica em cache: se uma falhar, as outras não são refeitas.

- **Calibrar o `shots`:** compilações têm muitos cortes. Se o número de planos parecer baixo ou alto demais,
  ajuste com `--scene-threshold` (menor = mais cortes; padrão 0.30). Vídeo contínuo sem cortes é dividido a cada
  `max_shot_s` (12 s) para ter onde cortar.
- **Custo no free tier:** ≈ 100 mil tokens por 10 min de vídeo (medido). O log mostra a estimativa antes de enviar.
  Há uma pausa entre janelas (`visual_pause_s`) por causa do limite de tokens por minuto.
- **Confira os nomes:** títulos como "Fulano nocauteia Sicrano" vêm do Gemini e podem estar errados. O prompt
  manda não citar nomes sem ter certeza, e o `review.md` avisa para conferir.

**Ajustar um corte:** edite `start`/`end`/`title`/`hook_text` em `selection.json` e rode
`python -m darkcnn render video.mp4` (re-renderiza só os cortes alterados; não usa Whisper nem Gemini).

**Cache:** a transcrição e a análise ficam em `workspace\<video>\`. A 2ª execução idêntica faz **zero**
chamadas ao Gemini e não renderiza de novo o que não mudou. O uso diário da API fica em `workspace\usage.json`
e há uma trava (`daily_request_budget`) contra estourar a cota.

## Limites conhecidos
- O perfil (`talk` ou `visual`) é escolhido por você com `--profile`; ainda não há detecção automática.
- Perfil de fala: uma única chamada de análise por vídeo, acima de ~40 min a qualidade pode cair (janelas na Fase 3).
  Perfil visual: já usa janelas, mas escolhe os melhores de cada janela sem uma 2ª passada comparando entre elas.
- Rastreio de rosto fica para a Fase 3. Se o YouTube mudar e o download falhar: `pip install -U yt-dlp`.

## Direitos autorais e plataformas
O pipeline não decide se você pode usar um vídeo. Ao baixar um link, ele registra a licença que o YouTube informa
(Creative Commons ou "licença padrão") e o `review.md` avisa quando é a licença padrão, que exige permissão do canal.
Com arquivo local, preencha `--source` e `--license`. Licenças CC-BY-ND (sem derivados) e CC-BY-NC (sem uso comercial) não servem para um canal
monetizado. Reeditar o vídeo de outra pessoa só com título e frase na tela, sem comentário ou transformação, é o
caso que o YouTube trata como *reused content* e que sistemas como o Content ID mais reivindicam; acrescente valor
real (narração, comentário, edição própria) e prefira fontes com permissão.

## Desenvolvimento
```powershell
pip install -e ".[dev,web]" ruff
ruff check src tests
pytest                                        # usa FFmpeg real em vídeo sintético; Whisper e Gemini são falsos
```
O CI (`.github/workflows/ci.yml`) roda ruff, pytest, o build da interface e o `docker build`.
`spikes/` guarda os testes de validação da Fase 0 (medem Whisper, tokens e timestamps do Gemini no seu PC).
