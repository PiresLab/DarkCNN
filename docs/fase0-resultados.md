# Fase 0 — resultados medidos

Máquina de teste: notebook Windows, Intel i7-10510U (4 núcleos), 16 GB RAM, **sem GPU dedicada**.
Vídeo de teste: ~5 min de fala PT-BR informal (podcast/conversa), trecho `--start 0 --dur 300`.
Data: 2026-10-07.

## Medidas

| Item | Resultado | Spike |
|---|---|---|
| Whisper `small` (faster-whisper, int8, CPU, beam 1) | **3,64x tempo real** (300 s em 82 s) -> ~16 min por hora de vídeo | 02b |
| Qualidade Whisper | Idioma PT detectado (prob 1,00); 1138 palavras; 8,5 % com confiança < 0,5; 6 palavras com duração 0; 0 fora de ordem | 02b |
| Legenda `.ass` com palavra destacada + marca d'água + crop/blur_fit em 1 encode | Aprovado visualmente pelo usuário (legenda "perfeita"; blur nítido) | 01 |
| Render x264 `medium`, 1080x1920, 30 s de vídeo | 39 s (crop) e 46 s (blur) -> ~1,3-1,5 s de render por s de vídeo | 01 |
| Tokens de **áudio** no Gemini (`count_tokens`) | 9.605 tokens para 300 s = **32,0/s** -> ~115 mil/h | 03 |
| Tokens de **vídeo** no Gemini (`count_tokens`, clipe 360p/5 fps, inclui o áudio) | 12.361 tokens para 120 s = **~103/s** -> ~371 mil/h | 03 |
| Transcrição pelo Gemini (`gemini-3.1-flash-lite`) | 65 segmentos em 17,5 s (~17x tempo real); sem tokens de "thinking" | 03 |
| Uso real vs `count_tokens` | `prompt_token_count` = 7.570 para os mesmos 300 s: parece 25 tokens/s + ~70 de texto, ou seja, **menor** que o `count_tokens` (a confirmar) | 03 |
| Tokens de saída na transcrição | 3.930 para 5 min -> ~47 mil/h (limite de saída do modelo: 65.536) | 03 |
| Erro de timestamp do Gemini vs Whisper | Início: mediana 0,48 s, p90 0,96 s, máx 11 s (outlier). Fim: mediana 0,49 s, p90 0,90 s. Viés médio -0,68 s (Gemini adianta) | 04 |

## Conclusões

1. **Arquitetura confirmada**: o Gemini escolhe por IDs de frase; os tempos vêm do Whisper local. Erro mediano de ~0,5 s e p90 de ~1 s no Gemini comeria palavras nos cortes e tiraria a legenda do ritmo.
2. **Whisper `small` em CPU basta** para o pipeline inteiro (16 min/h). `medium` fica opcional.
3. **Seleção por texto é de longe a mais barata**: ~15-20 mil tokens/h de texto (estimativa), contra ~90-115 mil em áudio e ~371 mil em vídeo.
4. **Vídeo é viável em janelas curtas** para o perfil `visual` (~371 mil tokens/h no clipe 360p), mas depende dos limites de TPM do projeto, que **ainda não foram medidos**.
5. **Gemini como transcritor** serve só de fallback sem tempo por palavra: rápido (17x), mas ±0,5 s e caro em tokens de saída (~47 mil/h).
6. Duração mínima por palavra na legenda (palavras com duração 0 no Whisper).

## Em aberto

- **Limites do free tier do projeto** (RPM/TPM/RPD de `gemini-3.1-flash-lite`): ainda não enviados. Definem o tamanho das janelas e o `daily_request_budget`.
- **Deriva do Gemini em áudio longo**: o valor calculado (13 s/h) vem de só 5 min com 1 outlier de 11 s; não é confiável. Não bloqueia nada, pois não usaremos tempos do Gemini.
- **Tokens de vídeo em resolução original** (o teste usou 360p).
- **Fonte real do conteúdo** (ex.: UFC) e licença: pendente de resposta do usuário.
- **Modelos**: `gemini-2.5-flash` tinha shutdown anunciado para 2026-10-16 (baixa confiança); o ID do modelo fica em config.
