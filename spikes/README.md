# Fase 0 — spikes de validação (código descartável)

Objetivo: trocar suposições por números medidos **no seu PC** (Windows + RX 7600) antes de escrever o MVP.
Nada aqui vira código de produto, exceto o gerador de `.ass` e o filtro único do spike 01, que serão reaproveitados.

## O que já foi validado (no container Linux, sem Whisper/Gemini reais)
| Item | Resultado |
|---|---|
| FFmpeg com libass/x264/overlay | OK; filtro único `crop`/`blur_fit` + `ass` + `overlay` gera 1080x1920 com **um** encode |
| Legenda `.ass` com palavra destacada | OK depois de corrigir estouro de largura (agrupa por ≤16 caracteres, quebra automática ligada) |
| Marca d'água PNG com alpha | OK depois de corrigir um PNG gerado todo transparente (`drawbox` sem `replace=1`) |
| Cálculo de erro/deriva (spike 04) | Recuperou viés e deriva plantados em dados sintéticos |
| Parse do JSON do whisper.cpp (spike 02) | **Só contra um JSON falso que eu escrevi seguindo minha memória do formato.** O formato real não foi confirmado |
| Chamadas Gemini (spike 03) | Monta o pedido e trata erros; **nunca rodou com chave válida** |

## Pré-requisitos (PowerShell)
```powershell
# 1. FFmpeg "full" (precisa de libass). Opção: winget
winget install Gyan.FFmpeg
# 2. Python 3.10+; dentro da pasta do repositório:
py -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r spikes\requirements.txt
# 3. Chave grátis em https://aistudio.google.com/apikey  -> copie .env.example para .env e preencha
# 4. whisper.cpp com Vulkan (AMD): baixe um binário Windows com Vulkan, se existir na página de
#    releases do projeto, ou compile com -DGGML_VULKAN=1 (precisa do Vulkan SDK). Não confirmei qual
#    é o nome do arquivo atual do release; se não achar, me diga e fazemos o fallback em CPU.
#    Modelo: ggml-large-v3-turbo.bin (ou ggml-medium.bin) do repositório de modelos do whisper.cpp.
```

## Passo a passo
Use um vídeo de **10 min ou mais, em PT-BR, com fala contínua**, de fonte que você pode usar (CC/permissão).
Use o **mesmo `--start`/`--dur`** nos spikes 02 e 03.

```powershell
python spikes\00_check_env.py --whisper-cli C:\whisper\whisper-cli.exe
python spikes\01_caption_watermark_test.py                      # sintético, sem internet
python spikes\02_whisper_words.py --input video.mp4 --model C:\whisper\ggml-large-v3-turbo.bin --whisper-cli C:\whisper\whisper-cli.exe --dur 600
python spikes\02_whisper_words.py ... --no-gpu                   # opcional: compara CPU x GPU
python spikes\01_caption_watermark_test.py --source video.mp4 --words spikes\out\whisper_words.json --dur 30   # legenda REAL: confira a sincronia assistindo
python spikes\03_gemini_probe.py --list-models
python spikes\03_gemini_probe.py --input video.mp4 --model <ID> --count-tokens --dur 600
python spikes\03_gemini_probe.py --input video.mp4 --model <ID> --transcribe --dur 600
python spikes\04_compare_timestamps.py
```

## O que me devolver
Cole os blocos **`=== RESUMO ===`** de cada script (02, 03, 04) e a lista de modelos do `--list-models`.
Se um script falhar, cole a mensagem inteira. Além disso, três coisas que só você consegue ver:
1. A página de limites do AI Studio (*Rate limits* do seu projeto): RPM, TPM e RPD do modelo escolhido.
2. Assista a `spikes\out\spike01\out_*.mp4` e ao preview real: a legenda está legível? Palavra destacada em sincronia?
3. Se algum comando do whisper.cpp usou GPU de fato (o resumo do 02 mostra as linhas do log sobre backend).

## Como cada número decide o plano
| Medida | Se for... | Então |
|---|---|---|
| Velocidade do Whisper na RX 7600 (02) | ≥ 5x tempo real | Whisper local em todo o vídeo, sem preocupação |
| | 1-5x | Transcreve tudo, mas com `medium`; ou só trechos candidatos |
| | < 1x ou GPU não usada | Cai para CPU/`small`, ou Gemini transcreve e Whisper só alinha os trechos escolhidos |
| Erro mediano do Gemini (04) | > 0,25 s ou deriva grande | Confirma: tempo preciso vem do Whisper, Gemini só escolhe por IDs de frase |
| | ≤ 0,25 s e sem deriva | Gemini pode ser fallback aceitável também para corte (ainda não para legenda por palavra) |
| Tokens de áudio/h (03) | ≈ 115 mil | Plano de janelas de 20-30 min confirmado |
| Tokens de vídeo/h (03) | ≈ 1 M | Vídeo só em janelas curtas e baixa resolução, como previsto |
| `p<0.5` no Whisper (02) | > 10% das palavras | Trocar de modelo ou aplicar `--dtw`/outro preset antes de usar na legenda |
