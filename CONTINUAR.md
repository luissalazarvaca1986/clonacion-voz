# Continuar: asistente por voz con avatar (Luisa) en el DGX Spark

Estado al 7-nov-2026, antes de reiniciar Claude Code para activar las herramientas de
Chrome (`/chrome`). Léelo entero antes de seguir.

## Objetivo

Web en **http://localhost:9090** que muestra solo a Luisa en el centro (con pantalla
completa) y:

1. escucha el micrófono todo el tiempo (o, sin micrófono, acepta texto escrito que entra
   como si lo hubiera transcrito el STT);
2. transcribe en español (whisper.cpp);
3. busca el conocimiento en el RAG de **SecondBrain** (`~/Proyectos/SecondBrain`, ya en
   marcha: Ollama :11434 con `nomic-embed-text`, ChromaDB :8100, backend :5000);
4. redacta la respuesta con **Qwen3.5 2B** (el mismo LLM que segmenta por emociones), con
   un máximo de ~2 minutos (260 palabras);
5. genera clips de voz (Qwen3-TTS, voz **Berenix** `447d4a974c1b`) y vídeo animado
   (SadTalker) del avatar **Luisa** con fondo **noche**, en *pipeline* y en paralelo;
6. reproduce los clips en orden sin cortes, sobre un bucle de Luisa en reposo.

El usuario pidió además: **medir el rendimiento de cada modelo y subir la calidad**
aprovechando el GB10 sin perder velocidad, y una arquitectura en paralelo que no deje
nunca de mostrar al avatar ni de escuchar.

## Equipo

- DGX Spark: NVIDIA **GB10** (Blackwell, sm_121), **ARM64**, 121 GB de memoria unificada,
  20 núcleos, driver 580 / CUDA 13.0.
- La GTX 1650 era provisional: **no hay que mantener compatibilidad** con ella.
- El usuario `rattio` ya está en el grupo `docker`. En shells antiguos usa
  `sg docker -c "docker ..."`.
- `sudo`: la contraseña está en `~/clave.txt`; úsala sin mostrarla:
  `sudo -S -p '' <cmd> < ~/clave.txt`.
- Git: identidad global de Luis Alfonso Salazar Vaca. El remoto va por **SSH**
  (`~/.ssh/id_ed25519_github`, ya registrada en GitHub). En esta red el puerto 22 está
  bloqueado, así que `~/.ssh/config` usa `ssh.github.com:443`. El trabajo está en la rama
  **`gb10`**.

## Hecho

### Adaptación a ARM64 / GB10
- `backend/Dockerfile`: compila `llama.cpp` b11529 (`llama-tts` + `llama-server`) con
  CUDA 13.0.1 para sm_121.
- `animador/Dockerfile`: Python 3.10 + torch 2.9.1+cu130 (ARM64), dependencias en
  `animador/requisitos-sadtalker.txt`. Incluye un parche de basicsr
  (`functional_tensor`) y `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1`.
- `stt/Dockerfile` (nuevo): `whisper.cpp` v1.9.5 `whisper-server` con CUDA, sm_121.
- Todos los `apt-get` llevan reintentos y tiempo máximo (antes se colgaban).

### Paralelismo
- `backend/app/servicios/sintesis.py`: el cerrojo global de la GPU es ahora un semáforo
  (`CLONAR_GPU_CONCURRENCIA`, en compose = 3).
- `animador/servidor.py`: semáforo `ANIMADOR_CONCURRENCIA` (= 3); `/salud` informa de
  la concurrencia.
- El backend usa el LLM externo `CLONAR_LLM_URL=http://clonar-voz-llm:8080` (siempre
  cargado en la GPU, `-np 4`).

### Servicios nuevos en `docker-compose.yml`
| Servicio | IP | Puerto | Qué hace |
|---|---|---|---|
| `clonar-voz-stt` | .16 | — | whisper-server, `-l es`, modelo `modelos-stt/ggml-large-v3-turbo.bin` |
| `clonar-voz-llm` | .17 | — | llama-server (imagen del backend) con `modelos-llm/Qwen3.5-2B-Q4_K_M.gguf` |
| `clonar-voz-conversa` | .18 | **9090** | web + orquestador (`conversa/`) |

### `conversa/` (nuevo)
- `app.py`: orquestador FastAPI.
  - `POST /api/turno` (con `audio`, o `texto` para el modo sin micrófono) y
    `GET /api/turno/{id}/eventos` (SSE: `transcripcion`, `contexto`, `texto`, `clip`,
    `total`, `error`, `fin`).
  - El LLM va en *streaming* y se corta en clips: el primero de ≥ 6 palabras, los
    siguientes de ≥ 25. Cada clip llama a `/api/generar` (modo emocional, vídeo animado,
    Luisa, noche, Berenix), con 3 clips en paralelo.
  - Una pregunta real nueva cancela el turno anterior de la misma sesión (*barge-in*).
  - Al arrancar genera el **bucle de reposo**: `/api/animaciones` con 8 s de silencio y
    `quieto`; queda registrado en `datos/conversa/`. Rutas `/reposo.mp4`, `/avatar.png`
    y `/videos/{mp4}` (aceptan GET y HEAD), y `/api/salud`.
- `static/index.html`: página única.
  - Capas: imagen fija < bucle de reposo < dos `<video>` alternos con precarga.
  - Micrófono siempre abierto: AudioWorklet, detección de voz con umbral adaptativo
    (más exigente mientras Luisa habla), envío de un WAV de 16 kHz.
  - Modo **«Escribir (sin micrófono)»**.
  - **Pantalla completa** con el botón, doble clic o la tecla F; la barra se oculta sola.
- `bench/medir.py`: mide el LLM (primer token, tokens/s), el TTS (factor de tiempo real)
  y el turno completo (tiempo hasta el primer clip).
- `bench/simulador.py`: la misma API con clips falsos, para probar la web sin modelos.
- `demo/vivero-las-acacias.md`: documento ficticio **ya cargado en SecondBrain**
  (colecciones `vivero` y `demo`, archivo_id `2cad5523-da76-4179-bb18-a15522da50f2`).

### Datos
- `avatares/Luisa/avatar.json`: voz cambiada a `447d4a974c1b` (Berenix); antes apuntaba a
  una voz que no existe.
- Modelos descargados y verificados por tamaño:
  - `modelo/`: TTS Q4_K_M, **Q8_0**, **BF16** y `mmproj` Q8_0 y **BF16**.
  - `modelos-llm/`: Qwen3.5-2B-Q4_K_M.
  - `modelos-stt/`: ggml-large-v3-turbo.
  - `modelos-animacion/`: SadTalker 256 y **512**, **GFPGANv1.4**, `parsing_parsenet`,
    alignment y detection.
- En el host: `ffmpeg`, y Playwright + Chromium *headless* en el venv del scratchpad (se
  pierde al reiniciar; reinstala con `pip install playwright && playwright install chromium`).

## Pendiente (en este orden)

1. Las 4 imágenes (backend, stt, animador, conversa) **ya compilaron**. Solo hay que
   reconstruir `clonar-voz-conversa`, porque sus últimos cambios (modo texto, HEAD) son
   posteriores a la imagen: `docker compose build clonar-voz-conversa`.
2. **Parar el simulador** si sigue en marcha (`docker rm -f conversa-sim`, que ocupa el
   9090) y levantar todo: `docker compose up -d`.
3. Revisar `docker compose ps` y los registros de `clonar-voz-llm`, `clonar-voz-stt`,
   `clonar-voz-backend`, `clonar-voz-animador` y `clonar-voz-conversa`. Comprobar
   `curl localhost:9090/api/salud` (todos los servicios a `true`) y que se crea el bucle
   de reposo.
4. **Probar en Chrome** (con `/chrome` activado) http://localhost:9090 → «Escribir (sin
   micrófono)». Preguntas de prueba: «¿A qué hora abren los sábados?», «¿Cuánto cuesta
   el envío?», «¿Cómo riego la lavanda?», y una fuera de tema para comprobar que no
   inventa. Verificar la cola de clips sin cortes, el reposo entre clips y la pantalla
   completa.
5. **Medir el rendimiento** con `conversa/bench/medir.py` (llm / tts / turno) y subir la
   calidad donde no penalice:
   - TTS: Q4_K_M → Q8_0 o BF16 + `mmproj` BF16. El modelo se elige en la configuración
     del backend (`/api/config` o autodetección en `backend/app/servicios/entorno.py`).
   - SadTalker: `--size 512` y `--enhancer gfpgan` en `animador/servidor.py` (`_comando`;
     ahora está fijo en 256 y sin realce).
   - Whisper: large-v3-turbo frente a large-v3, si el tiempo lo permite.
   - LLM: valorar un Qwen3.5 mayor solo para redactar (el usuario pidió usar «el modelo
     de la segmentación», así que conviene **consultarlo** antes de cambiarlo).
   - Ajustar `CONVERSA_PALABRAS_CLIP` y las concurrencias según el tiempo real medido.
6. Actualizar el README (sección de conversación y despliegue en el DGX Spark) y, si el
   usuario lo pide, hacer commit. **No se ha hecho ningún commit.**
