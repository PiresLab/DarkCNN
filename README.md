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

## Instalação (Windows, PowerShell)
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
| `--text-mode captions` | legenda por palavra, palavra falada em amarelo (padrão) |
| `--text-mode titled` | **título no topo + frase-gancho embaixo**, sem legenda de fala (nocautes, vídeos satisfatórios) |
| `--text-mode none` | sem texto |
| `--layout blur` / `crop` | vídeo inteiro sobre fundo desfocado (padrão) / corte central 9:16 |
| `--watermark logo.png` | PNG com alpha (posição e opacidade no `config.yaml`) |
| `--clips 5 --min 30 --max 60` | quantidade e duração dos cortes |
| `--model ID` | modelo Gemini (veja os IDs com `python spikes\03_gemini_probe.py --list-models`) |
| `--preset veryfast` | render mais rápido, arquivo maior (padrão `medium`) |
| `--force` | ignora o cache de transcrição e de análise |

**Ajustar um corte:** edite `start`/`end`/`title`/`hook_text` em `selection.json` e rode
`python -m darkcnn render video.mp4` (re-renderiza só os cortes alterados; não usa Whisper nem Gemini).

**Cache:** a transcrição e a análise ficam em `workspace\<video>\`. A 2ª execução idêntica faz **zero**
chamadas ao Gemini e não renderiza de novo o que não mudou. O uso diário da API fica em `workspace\usage.json`
e há uma trava (`daily_request_budget`) contra estourar a cota.

## Limites conhecidos (Fase 1)
- A seleção ainda depende da **fala** (transcrição). Vídeos **sem fala** (nocautes, satisfatórios) entram na
  Fase 2: hoje o modo `--text-mode titled` já coloca título e gancho, mas a escolha dos trechos precisa de áudio falado.
- Uma única chamada de análise por vídeo: acima de ~40 min a qualidade pode cair (janelas na Fase 3).
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
pip install -e ".[dev]"
pytest                                        # usa FFmpeg real em vídeo sintético; Whisper e Gemini são falsos
```
`spikes/` guarda os testes de validação da Fase 0 (medem Whisper, tokens e timestamps do Gemini no seu PC).
