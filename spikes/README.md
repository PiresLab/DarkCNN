# Fase 0 — spikes de validação (código descartável)

Objetivo: trocar suposições por números medidos **no seu PC** (notebook Windows, só CPU) antes de escrever o MVP.
Nada aqui vira código de produto, exceto o gerador de `.ass` e o filtro único do spike 01, que serão reaproveitados.

## O que já foi validado (no container Linux, sem Whisper/Gemini reais)
| Item | Resultado |
|---|---|
| FFmpeg com libass/x264/overlay | OK; filtro único `crop`/`blur_fit` + `ass` + `overlay` gera 1080x1920 com **um** encode |
| Legenda `.ass` com palavra destacada | OK depois de corrigir estouro de largura (agrupa por ≤16 caracteres, quebra automática ligada) |
| Marca d'água PNG com alpha | OK depois de corrigir um PNG gerado todo transparente (`drawbox` sem `replace=1`) |
| Cálculo de erro/deriva (spike 04) | Recuperou viés e deriva plantados em dados sintéticos |
| Spike 02b (faster-whisper, só pip) | API conferida no pacote real (v1.2.1); fluxo testado com um modelo **falso**. Não consegui baixar modelo aqui, então a transcrição real é nova para nós |
| Parse do JSON do whisper.cpp (spike 02) | **Só contra um JSON falso que eu escrevi seguindo minha memória do formato.** O formato real não foi confirmado. Spike 02 é opcional: use o 02b |
| Chamadas Gemini (spike 03) | Monta o pedido e trata erros; **nunca rodou com chave válida** |

## Pré-requisitos (PowerShell)
```powershell
# 1. FFmpeg "full" (precisa de libass). Opção: winget
winget install Gyan.FFmpeg
# 2. Python 3.10+; dentro da pasta do repositório:
py -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r spikes\requirements.txt
# 3. Chave grátis em https://aistudio.google.com/apikey  -> copie .env.example para .env e preencha
# 4. Whisper: nada a instalar à parte. O faster-whisper já veio no passo 2 e roda em CPU.
#    O modelo é baixado sozinho na primeira execução (small ≈ 0,5 GB; medium ≈ 1,5 GB).
#    Alternativa avançada (whisper.cpp / GPU AMD com Vulkan): ver spike 02; deixe para depois.
```

## Passo a passo
Use um vídeo de **5 min ou mais, em PT-BR, com fala contínua**, de fonte que você pode usar (CC/permissão).
Use o **mesmo `--start`/`--dur`** nos spikes 02b e 03.

```powershell
python spikes\00_check_env.py
python spikes\01_caption_watermark_test.py                      # sintético, sem internet
python spikes\02b_faster_whisper_words.py --input video.mp4 --model small --dur 300   # 1ª vez baixa o modelo
python spikes\02b_faster_whisper_words.py --input video.mp4 --model medium --dur 300  # opcional: mais preciso, mais lento
python spikes\01_caption_watermark_test.py --source video.mp4 --words spikes\out\whisper_words.json --dur 30   # legenda REAL: confira a sincronia assistindo
python spikes\03_gemini_probe.py --list-models
python spikes\03_gemini_probe.py --input video.mp4 --model <ID> --count-tokens --dur 300
python spikes\03_gemini_probe.py --input video.mp4 --model <ID> --transcribe --dur 300
python spikes\04_compare_timestamps.py
```

## O que me devolver
Cole os blocos **`=== RESUMO ===`** de cada script (02b, 03, 04) e a lista de modelos do `--list-models`.
Se um script falhar, cole a mensagem inteira. Além disso, três coisas que só você consegue ver:
1. A página de limites do AI Studio (*Rate limits* do seu projeto): RPM, TPM e RPD do modelo escolhido.
2. Assista a `spikes\out\spike01\out_*.mp4` e ao preview real: a legenda está legível? Palavra destacada em sincronia?
3. O tempo que o 02b levou no seu notebook (o resumo já calcula a estimativa para 1 hora de vídeo).

## Como cada número decide o plano
| Medida | Se for... | Então |
|---|---|---|
| Velocidade do Whisper em CPU (02b; a linha "estimativa p/ 1 hora") | ≤ ~30 min por hora de vídeo | `small` serve para o pipeline inteiro |
| | 30-90 min por hora | Aceitável rodando em segundo plano/à noite, ou só nos trechos escolhidos pelo Gemini |
| | > 90 min por hora | Gemini transcreve (sem tempo por palavra) e o Whisper alinha só os trechos escolhidos; ou `base` |
| Erro mediano do Gemini (04) | > 0,25 s ou deriva grande | Confirma: tempo preciso vem do Whisper, Gemini só escolhe por IDs de frase |
| | ≤ 0,25 s e sem deriva | Gemini pode ser fallback aceitável também para corte (ainda não para legenda por palavra) |
| Tokens de áudio/h (03) | ≈ 115 mil | Plano de janelas de 20-30 min confirmado |
| Tokens de vídeo/h (03) | ≈ 1 M | Vídeo só em janelas curtas e baixa resolução, como previsto |
| `p<0.5` no Whisper (02b) | > 10% das palavras | Subir de modelo (`small` -> `medium`) antes de usar na legenda |
