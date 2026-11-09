# TO_FIX — pendientes para Claude en el DGX Spark (rama `gb10`)

Escrito el 8-nov-2026 desde el equipo de desarrollo (GTX 1650, x86, rama `main`).
Léelo entero antes de tocar nada. Sustituye a `CONTINUAR.md` en lo que se contradigan.

## Contexto

- **Dos equipos, dos ramas, el mismo código de app.**
  - `main` = desarrollo (GTX 1650, 4 GB, x86): todo en CPU con modelos ligeros.
  - `gb10` = producción (DGX Spark, GB10 Blackwell sm_121, **ARM64**, CUDA 13, 121 GB
    unificados): todo en GPU con modelos grandes.
- **Regla del usuario:** lo que se haga en un equipo tiene que funcionar en el otro.
  Nada de modelos fijos en el código: todo por variables de entorno, compose o
  descubrimiento (carpetas de modelos, `/v1/models`, APIs del backend).
- Al pasar cambios entre ramas se copia **solo el código de la app** (`backend/app`,
  `conversa/`, `animador/servidor.py`, la web y los avatares). **Nunca** se copian los
  Dockerfiles (`backend/`, `stt/`, `animador/`), `animador/requisitos-sadtalker.txt` ni
  los valores de concurrencia o GPU del compose. Los de `gb10` son ARM64/CUDA 13; los de
  `main` son x86/CPU.
- `sudo`: la contraseña está en `~/clave.txt`. Úsala **sin leerla ni mostrarla**:
  `sudo -S -p '' <cmd> < ~/clave.txt`. Docker en shells viejos: `sg docker -c "..."`.
- Todo en español: interfaz, código, comentarios y commits. La red Docker es
  `192.168.93.0/24` (IPs .10 a .18) y la web va en el puerto 9090.

## Qué cambió en el commit `f032b14` (sin probar en el Spark)

1. **Pantalla de entrada** (estilo «unirse a una reunión» de Teams, en `conversa/static/index.html`):
   - Selector de avatar arriba y vista previa en el centro.
   - Botón «Entrar a la sala» y, a su lado, los fondos día/noche/lluvia (solo con el
     vídeo encendido).
   - Interruptores de vídeo y micrófono, y «Configuración personalizada» (un selector de
     modelo por función: STT, LLM, TTS y animación).
   - «Ajustes adicionales»: salida de audio (`setSinkId`) y «Noise Reduction» (ver más abajo).
   - Con el vídeo apagado se ve el fondo de color con el círculo e inicial, no el avatar.
2. **Configuración en SQLite** (`conversa/ajustes.py`, `datos/conversa/conversa.sqlite3`).
   La primera vez se propone lo primero disponible de cada lista; al entrar se valida y
   se guarda, y es la de defecto la próxima vez.
   - `GET /api/opciones`: avatares utilizables, `no_disponibles`, modelos por función,
     `guardada` y `propuesta`.
   - `PUT /api/configuracion`: valida, guarda, carga el STT con `whisper /load` y prepara
     la sala.
3. **Sala única**, igual con micrófono o sin él:
   - Avatar en 16:9 que se ajusta al alto disponible; botón de pantalla completa (solo
     icono) abajo a la izquierda del avatar.
   - Línea de estado entre el avatar y el pie.
   - Pie fijo con interruptores de vídeo y micrófono, engranaje (modelos), campo de texto
     con avión de papel y salir (rojo al pasar el ratón).
   - Con el micrófono, cada frase va a `POST /api/transcribir` y se añade al campo; tras
     ~2,5 s de silencio se envía sola si no está vacía, y también se puede enviar con Intro.
4. **Perfil por avatar.** El bucle de reposo y el relleno «Déjame pensarlo…» se generan
   por avatar, fondo y modelo TTS. El reposo se genera **solo si el vídeo está encendido**.
5. **Backend.** `modelo_tts` por petición en `/api/generar`. `GET /api/modelo/disponibles`
   devuelve cada TTS con su mmproj emparejado. `CLONAR_TTS_MODELO`, `CLONAR_TTS_MMPROJ` y
   `CLONAR_TTS_DISPOSITIVO` son opcionales. El cliente LLM envía `model` (el cargado en el
   router). Una síntesis **en CPU y sin vídeo** ya no espera el cerrojo de GPU; en el Spark
   la voz va en GPU, así que no cambia nada.
6. **Animador:** `ANIMADOR_DISPOSITIVO` (por defecto `gpu`).
7. **Compose de `gb10`:**
   - `clonar-voz-llm` pasa a **modo router**: `--models-dir /modelos --models-max
     ${LLM_MAX_MODELOS:-2} -ngl 999 -fa on`.
   - `clonar-voz-conversa` monta `./modelos-stt` y `./conversa/demo` (conocimiento de
     respaldo si SecondBrain no responde).
   - Se quitaron `CONVERSA_AVATAR` y `CONVERSA_VOZ`: ahora se eligen en la web.
8. **Datos:** el avatar Luis usa la voz `ddfbb4fc3c39` («Luis Salazar»); Luisa usa Berenix.

## Para levantarlo en el Spark

```bash
cd ~/Proyectos/"Clonacion Voz"      # o donde esté el repo
git checkout gb10 && git pull
# Si Docker creó datos/conversa como root, la configuración no se guarda (Permission denied):
sudo -S -p '' chown -R 1000:1000 datos/conversa < ~/clave.txt
docker compose build clonar-voz-backend clonar-voz-conversa   # el código del backend cambió
docker compose up -d
docker compose ps
curl -s localhost:9090/api/salud | python3 -m json.tool
curl -s localhost:9090/api/opciones | python3 -m json.tool
```

## Qué verificar o corregir, por orden

1. **El LLM en modo router con GPU.** En la 1650 solo se probó en CPU, con el mismo
   llama.cpp b11529.
   - `curl -s 192.168.93.17:8080/v1/models`: comprueba que en `status.args` aparecen
     `-ngl 999` y `-fa on`, porque el router los pasa a cada modelo.
   - `/health` debe responder `ok`, porque lo usa el healthcheck.
   - Si el router falla con CUDA, vuelve a un solo modelo (`-m /modelos/...gguf --alias
     ...`). El código funciona con ambos, pero entonces la web solo ofrecerá ese LLM.
2. **Cambio de Whisper en caliente.** `conversa` hace `POST {stt}/load` con el campo
   multipart `model=/modelos/<archivo>`. Se probó la lista de modelos, no el `/load`
   en GPU. Si añades `ggml-large-v3.bin` a `modelos-stt/`, aparecerá en la web: pruébalo.
3. **Emparejamiento TTS y mmproj.** Está en `backend/app/servicios/entorno.py`,
   `listar_modelos_tts()`.
   - Hoy empareja por el nombre base sin la cuantización y toma el **primer** mmproj que
     coincida (por orden, `mmproj-…-Q8_0` antes que `mmproj-…-bf16`). Así, el modelo BF16
     acabaría con el mmproj Q8_0.
   - **Mejora pendiente:** preferir el mmproj de la misma cuantización (BF16 con BF16), y
     si no hay, el de mayor calidad.
   - El primero de la lista (la opción por defecto) sale de `CLONAR_TTS_MODELO` o, si no
     está, del primero ordenado (Q4_K_M). En el Spark conviene `TTS_MODELO` o un orden por
     calidad, para que la primera vez se proponga Q8_0 o BF16. Hay que exponerlo en el
     compose de `gb10` (variables `TTS_MODELO` y `TTS_MMPROJ`, que hoy solo están en `main`).
4. **Primera entrada con vídeo.** Genera el bucle de reposo de esa combinación de avatar
   y fondo, más el relleno con vídeo. En la 1650 en CPU tardó ~13 min; aquí debería ser
   mucho menos.
   - Revisa `docker logs clonar-voz-conversa | grep -i reposo`.
   - Ya hay un `datos/conversa/reposo-Luisa-noche.json` si existía de antes: la clave es
     `avatar-escena`, así que es compatible.
5. **Benchmarks.**
   - `conversa/bench/medir.py` ya envía `model` (corregido en este commit). Vuelve a
     medir: LLM, TTS y turno.
   - `conversa/bench/simulador.py` está **desfasado**: no tiene `/api/opciones`,
     `/api/configuracion` ni `/api/transcribir`, así que la web nueva no arranca con él.
     Actualízalo si hace falta probar la web sin modelos.
6. **Pendientes heredados de `CONTINUAR.md`** que siguen abiertos:
   - SadTalker a `--size 512` y `--enhancer gfpgan` (`animador/servidor.py`, `_comando`).
     Hoy está fijo en 256 y `CAPACIDADES.tamano` solo ofrece 256. Hazlo configurable por
     entorno para no romper la 1650.
   - Subir la calidad del TTS (Q8_0/BF16 con mmproj BF16) sin perder velocidad, midiendo
     con `medir.py`.
   - Ajustar `CONVERSA_PALABRAS_CLIP` y las concurrencias según lo medido.
7. **Prueba completa en Chrome** (`/chrome` en Claude Code) en http://localhost:9090:
   - Entra con cada avatar, con y sin vídeo, y prueba los tres fondos.
   - Cambia el LLM y el TTS desde el engranaje dentro de la sala.
   - Prueba el micrófono: frase → campo → envío tras la pausa.
   - Comprueba que la configuración persiste al recargar.
   - Prueba la «Simulación de movimiento» con Luisa y con Luis, y la calibración del
     Noise Reduction.
8. **Simulación de movimiento** (commit «Simulación de movimiento y Noise Reduction»).
   Es la alternativa ligera al vídeo con IA y la primera opción de «Animación del avatar».
   El navegador anima la foto con una malla WebGL sobre los puntos de la cara; la boca
   sigue la voz y hay parpadeos y vaivén. Al backend solo se le pide la voz.
   - Cada avatar necesita `avatares/<id>/rostro.json` (98 puntos WFLW y la `posicion` del
     avatar). Los de Luisa y Luis ya están versionados. Un avatar sin ese archivo, o con
     la posición cambiada, sale deshabilitado («sin puntos del rostro»).
   - Para generar el de un avatar nuevo, sigue la cabecera de `herramientas/rostro.py`. Se
     ejecuta en el contenedor del animador con facexlib y los pesos de
     `modelos-animacion/gfpgan/weights`. **Verifica que funciona con el animador del GB10**
     (Python 3.10 + torch 2.9): en la 1650 se probó con Python 3.8 + torch 1.12. Después
     añade `avatar` y `posicion` (de `/api/avatares`) al JSON; ver cómo se hizo en el commit.
   - `conversa` monta `./avatares:/avatares:ro` (ya añadido al compose de `gb10`).
   - Pruébala en Chrome. La apertura de la boca (5,5 % del alto de la cara) y la curva
     adaptativa del volumen ya las aprobó el usuario; no las cambies sin consultarle.
9. **Noise Reduction** (hecho; pruébalo con micrófono en el Spark). Al activarlo se abre una
   calibración: 10 s de ruido de la sala y 10 s de voz. Con eso se calculan el umbral de
   voz intencional, el filtro de banda de voz y la resta espectral del ruido antes de
   Whisper. Apagado = funcionamiento anterior, tal cual. Está en la entrada (Ajustes
   adicionales) y en la sala (engranaje). La calibración depende del micrófono y del lugar:
   en el Spark hay que calibrar de nuevo.

## Al terminar

- Commit en `gb10` con la autoría del usuario (Luis Alfonso Salazar Vaca), en español.
- Si corriges código de la app (no de la infraestructura), llévalo también a `main` para
  que las dos ramas sigan con el mismo código.

## Estado al 9-nov-2026 (DGX Spark, rama `gb10`)

1. **LLM en modo router: OK.** `/v1/models` muestra `--n-gpu-layers 999` y `--flash-attn on`; `/health` = ok; CUDA 13.0.1.
2. **Whisper `/load` en GPU: OK** (CUDA0, 1,6 GB, 0,3 s). Solo hay `ggml-large-v3-turbo.bin`; falta descargar `ggml-large-v3.bin` para probar el cambio entre modelos.
3. **TTS y mmproj: corregido** (`entorno.py`). Se prefiere el mmproj de la misma cuantización y, si no hay, el de mayor calidad: Q8_0→Q8_0, BF16→BF16, Q4_K_M→BF16. El compose de `gb10` expone `TTS_MODELO`/`TTS_MMPROJ` con Q8_0 por defecto.
4. **Primera entrada con vídeo: OK** (ya existía `reposo-Luisa-noche.json`; `/api/salud` → `reposo: true`).
5. **Benchmarks** (`medir.py`): LLM 28 ms al primer token y 129 tokens/s; TTS Q8_0 factor 0,51–0,55×; turno completo 7,5 s para 9,5 s de respuesta. TTS por cuantización (mediana de 3): Q4_K_M 0,69×, **Q8_0 0,59×**, BF16 0,75× → Q8_0 por defecto. `simulador.py` sigue desfasado (no se tocó).
6. **SadTalker configurable** (`animador/servidor.py`): `ANIMADOR_TAMANOS`, `ANIMADOR_TAMANO` y `ANIMADOR_MEJORA` (por defecto 256 sin mejora; en `gb10`, 512 con GFPGAN). Medido en el GB10 con 7 s de audio: 512+GFPGAN ≈ 125 s (salida 1024×1024); 256 sin mejora 33 s.
7. **Prueba en Chrome:** desde otro equipo de la red (http://IP:9090) la web se quedaba en «Cargando…» porque `crypto.randomUUID` solo existe en https/localhost. Corregido en `conversa/static/index.html` con un respaldo de `getRandomValues`. Entrada → sala → turno por texto con Luisa (noche, simulación): OK, turno de 8,2 s; la configuración persiste. **Pendiente:** micrófono y Noise Reduction (requieren https o localhost: probar en el propio Spark o servir la web con https), los otros fondos, Luis y el engranaje. Detalle visual: el botón de pantalla completa se monta sobre la etiqueta con el nombre del avatar.
8. **`herramientas/rostro.py` en el GB10: OK** (Python 3.10, torch 2.9.1+cu130): da los mismos 98 puntos de Luisa que el archivo versionado (diferencia 0,00 px).
9. **Noise Reduction:** pendiente de probar con micrófono (ver 7).

## Pendiente nuevo (9-nov-2026): el historial contamina las respuestas

Probado por voz en la sala con Luisa y el conocimiento del SecondBrain:

- En una sesión nueva responde bien («¿Qué es SentinelOne?», «¿Qué es NinjaOne?»), con las fuentes correctas.
- En la **misma sesión**, la pregunta siguiente se contesta sobre el tema anterior: tras preguntar por SentinelOne, «¿Qué es NinjaOne?» respondió «SentinelOne es…»; y tras una respuesta errónea sobre Rattio, «¿Qué es SentinelOne?» empezó con «Rattio no es SentinelOne…».
- Causa probable: `conversa/app.py` mete los últimos `HISTORIAL_TURNOS = 4` turnos (8 mensajes) junto al contexto recuperado, y el Qwen3.5 2B se deja arrastrar por el historial.

Opciones, de menor a mayor esfuerzo (medir con `medir.py turno` y probar en la sala):
1. Bajar `HISTORIAL_TURNOS` (1 o 2) o hacerlo configurable por entorno (`CONVERSA_HISTORIAL_TURNOS`), para no romper la 1650.
2. En el prompt, poner la pregunta actual y su contexto **después** del historial e indicar que se responda solo a la última pregunta con ese contexto.
3. Usar un LLM más grande en el Spark (en `modelos-llm/` solo hay Qwen3.5-2B-Q4_K_M; hay memoria para 30B o más) y dejar el 2B para la 1650.

Además: «¿Qué es Rattio?» responde mal («plataforma de gestión de identidad») porque la búsqueda no trae la colección `rattio`; Juan Carlos va a cargar un `.md` de Rattio nuevo.
