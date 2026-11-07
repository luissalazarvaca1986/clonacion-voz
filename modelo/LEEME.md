# Carpeta del modelo

Volumen montado en el backend como `/app/modelo`. Aquí van los **dos** archivos
`.gguf` que necesita la aplicación. No están en el repositorio porque pesan
alrededor de 1,5 GB.

| Archivo | Qué es |
|---|---|
| `Qwen3-TTS-12Hz-1.7B-Base-Q4_K_M.gguf` | el modelo de lenguaje que produce los tokens de audio |
| `mmproj-Qwen3-TTS-12Hz-1.7B-Base-Q8_0.gguf` | el proyector/vocoder que convierte esos tokens en sonido |

**Hacen falta los dos.** Sin el `mmproj`, `llama-tts` carga el modelo pero no
genera ningún audio.

## Cómo conseguirlos

1. El panel «Primer arranque» de la web (http://localhost:8094) aparece solo si faltan.
2. Desde el contenedor:
   `docker compose exec clonar-voz-backend python -m app.servicios.huggingface --dir /app/modelo`
3. A mano desde
   <https://huggingface.co/ggml-org/Qwen3-TTS-12Hz-1.7B-Base-GGUF/tree/main>,
   dejándolos en esta carpeta sin renombrarlos.

La descarga es reanudable y verifica el SHA-256 de cada archivo al terminar.
Hay otras cuantizaciones (Q8_0 y BF16); en una GTX 1650 de 4 GB la BF16 no cabe.
