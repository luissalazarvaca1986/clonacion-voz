# -*- coding: utf-8 -*-
"""Conversa: asistente por voz con avatar, en pipeline.

Cada turno:
  micrófono (WAV 16 kHz) → whisper.cpp (español) → RAG de SecondBrain (Ollama + ChromaDB)
  → Qwen3.5 2B redacta la respuesta EN STREAMING → la respuesta se corta en clips de
  una o varias frases → cada clip va al backend del clonador (/api/generar en modo
  emocional: segmentación con el LLM, voz con Qwen3-TTS y animación con SadTalker)
  con varios clips en paralelo → la web reproduce los clips en orden, sin huecos.

Así Luisa empieza a hablar en cuanto está listo el primer clip, mientras el resto se
sigue generando. Entre turnos la web reproduce un bucle de Luisa en reposo, y el
micrófono no se cierra nunca: si el usuario habla mientras Luisa responde, el turno
nuevo sustituye al anterior (que se cancela) en cuanto hay una transcripción real.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("conversa")

# ---- Configuración -----------------------------------------------------------
STT_URL = os.getenv("CONVERSA_STT_URL", "http://clonar-voz-stt:8080")
LLM_URL = os.getenv("CONVERSA_LLM_URL", "http://clonar-voz-llm:8080")
BACKEND_URL = os.getenv("CONVERSA_BACKEND_URL", "http://clonar-voz-backend:8080")
OLLAMA_URL = os.getenv("CONVERSA_OLLAMA_URL", "http://host.docker.internal:11434")
CHROMA_URL = os.getenv("CONVERSA_CHROMA_URL", "http://host.docker.internal:8100")
EMBEDDING = os.getenv("CONVERSA_EMBEDDING", "nomic-embed-text")
AVATAR = os.getenv("CONVERSA_AVATAR", "Luisa")
VOZ = os.getenv("CONVERSA_VOZ", "447d4a974c1b")          # Berenix
ESCENA = os.getenv("CONVERSA_ESCENA", "noche")
ANIMADO = os.getenv("CONVERSA_ANIMADO", "1") == "1"
# ~2,3 palabras por segundo en español: 260 palabras ≈ 2 minutos de voz
MAX_PALABRAS = int(os.getenv("CONVERSA_MAX_PALABRAS", "260"))
# El primer clip es corto para que Luisa empiece a hablar cuanto antes; los demás,
# más largos para amortizar el arranque de cada proceso de animación.
PALABRAS_PRIMER_CLIP = int(os.getenv("CONVERSA_PALABRAS_PRIMER_CLIP", "6"))
PALABRAS_CLIP = int(os.getenv("CONVERSA_PALABRAS_CLIP", "25"))
CLIPS_EN_PARALELO = int(os.getenv("CONVERSA_CLIPS_EN_PARALELO", "3"))
FRAGMENTOS = int(os.getenv("CONVERSA_FRAGMENTOS", "6"))
SEGUNDOS_REPOSO = float(os.getenv("CONVERSA_SEGUNDOS_REPOSO", "8"))
DIR_VIDEOS = Path(os.getenv("CONVERSA_DIR_VIDEOS", "/videos"))
DIR_DATOS = Path(os.getenv("CONVERSA_DIR_DATOS", "/datos"))
HISTORIAL_TURNOS = 4
CHROMA_BASE = "/api/v2/tenants/default_tenant/databases/default_database"

# Frases que Whisper «oye» en el silencio o el ruido
_ALUCINACIONES = re.compile(
    r"subt[ií]tulos|amara\.org|suscr[ií]bete|gracias por ver|^\W*$", re.I)
_FIN_FRASE = re.compile(r"(?<=[.!?…])[\"»”)]*\s+")

_SISTEMA = """Eres Luisa, una asistente virtual que conversa por voz con las personas.
Respondes SIEMPRE en español neutro, con un tono cercano, amable y profesional.

Reglas:
- Basa tu respuesta en la INFORMACIÓN DE REFERENCIA. Si la respuesta no está ahí, dilo con
  naturalidad y ofrece ayuda con otra cosa; nunca inventes datos, precios ni horarios.
- Tu respuesta se convertirá en voz: escribe frases completas y naturales, como si hablaras.
  Nada de listas, viñetas, asteriscos, tablas, emojis, enlaces ni formato markdown.
- Empieza con una frase breve que responda directamente; después amplía si hace falta.
- Sé breve: entre una y cinco frases suele bastar. Nunca superes {max_palabras} palabras.
- No menciones la «información de referencia», los «documentos» ni que eres un modelo."""


# ---- Turnos ------------------------------------------------------------------
@dataclass
class Turno:
    id: str
    sesion: str
    eventos: asyncio.Queue = field(default_factory=asyncio.Queue)
    terminado: bool = False
    cancelado: bool = False
    creado: float = field(default_factory=time.time)
    tareas_backend: set[str] = field(default_factory=set)
    trabajos: list[asyncio.Task] = field(default_factory=list)

    async def emitir(self, tipo: str, **datos: Any) -> None:
        await self.eventos.put({"tipo": tipo, **datos})


TURNOS: dict[str, Turno] = {}
TURNO_ACTIVO: dict[str, str] = {}          # sesión → turno que está respondiendo
CONVERSACIONES: dict[str, list[dict[str, str]]] = {}
CLIPS = asyncio.Semaphore(CLIPS_EN_PARALELO)

app = FastAPI(title="Conversa", docs_url="/api/docs", openapi_url="/api/openapi.json")


def _purgar() -> None:
    limite = time.time() - 900
    for clave in [k for k, t in TURNOS.items() if t.terminado and t.creado < limite]:
        TURNOS.pop(clave, None)


async def _cancelar_turno(turno: Turno) -> None:
    if turno.cancelado or turno.terminado:
        return
    turno.cancelado = True
    for trabajo in turno.trabajos:
        trabajo.cancel()
    async with httpx.AsyncClient(timeout=5) as cliente:
        for id_tarea in list(turno.tareas_backend):
            try:
                await cliente.post(f"{BACKEND_URL}/api/tarea/{id_tarea}/cancelar")
            except httpx.HTTPError:
                pass
    log.info("Turno %s cancelado (lo sustituye otro)", turno.id)


# ---- Pasos del turno -----------------------------------------------------------
async def transcribir(cliente: httpx.AsyncClient, wav: bytes) -> str:
    r = await cliente.post(f"{STT_URL}/inference",
                           files={"file": ("voz.wav", wav, "audio/wav")},
                           data={"response_format": "json", "temperature": "0",
                                 "language": "es"}, timeout=120)
    r.raise_for_status()
    texto = " ".join((r.json().get("text") or "").split())
    texto = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", texto).strip()   # [Música], (risas)…
    return "" if _ALUCINACIONES.search(texto) or len(texto.split()) < 1 or len(texto) < 3 \
        else texto


async def buscar_contexto(cliente: httpx.AsyncClient, pregunta: str) -> list[dict[str, Any]]:
    """Búsqueda semántica en todas las colecciones de SecondBrain, en paralelo y sin duplicados."""
    r = await cliente.post(f"{OLLAMA_URL}/api/embed",
                           json={"model": EMBEDDING, "input": pregunta}, timeout=30)
    r.raise_for_status()
    vector = r.json()["embeddings"][0]
    r = await cliente.get(f"{CHROMA_URL}{CHROMA_BASE}/collections", timeout=10)
    r.raise_for_status()

    async def consultar(col: dict[str, Any]) -> dict[str, Any] | None:
        try:
            q = await cliente.post(
                f"{CHROMA_URL}{CHROMA_BASE}/collections/{col['id']}/query",
                json={"query_embeddings": [vector], "n_results": FRAGMENTOS,
                      "include": ["documents", "metadatas", "distances"]}, timeout=15)
            q.raise_for_status()
            return q.json()
        except httpx.HTTPError as e:
            log.warning("Colección %s: %s", col.get("name"), e)
            return None

    mejores: dict[str, dict[str, Any]] = {}
    for datos in await asyncio.gather(*(consultar(c) for c in r.json())):
        if not datos:
            continue
        for i, id_trozo in enumerate(datos["ids"][0]):
            distancia = datos["distances"][0][i]
            if id_trozo not in mejores or distancia < mejores[id_trozo]["distancia"]:
                meta = datos["metadatas"][0][i] or {}
                mejores[id_trozo] = {"texto": datos["documents"][0][i], "distancia": distancia,
                                     "documento": meta.get("nombre", "")}
    return sorted(mejores.values(), key=lambda m: m["distancia"])[:FRAGMENTOS]


def limpiar_para_voz(texto: str) -> str:
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.S)
    texto = re.sub(r"[*_#`>|~]+", "", texto)
    texto = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", texto, flags=re.M)
    texto = re.sub(r"https?://\S+", "", texto)
    texto = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", texto)
    return " ".join(texto.split())


async def frases_del_llm(cliente: httpx.AsyncClient, sesion: str, pregunta: str,
                         fragmentos: list[dict[str, Any]]) -> AsyncIterator[str]:
    """Respuesta del LLM en streaming, entregada frase a frase en cuanto se completa cada una."""
    referencia = "\n\n".join(f"[{f['documento']}]\n{f['texto']}" for f in fragmentos) \
        or "(no hay información disponible)"
    mensajes = [{"role": "system", "content": _SISTEMA.format(max_palabras=MAX_PALABRAS)}]
    mensajes += CONVERSACIONES.get(sesion, [])[-2 * HISTORIAL_TURNOS:]
    mensajes.append({"role": "user", "content":
                     f"INFORMACIÓN DE REFERENCIA:\n{referencia}\n\nPREGUNTA: {pregunta}"})
    pendiente = ""
    async with cliente.stream("POST", f"{LLM_URL}/v1/chat/completions", json={
        "messages": mensajes, "temperature": 0.4, "top_p": 0.9, "stream": True,
        "max_tokens": int(MAX_PALABRAS * 2.2),
        "chat_template_kwargs": {"enable_thinking": False},
    }, timeout=httpx.Timeout(10, read=120)) as r:
        r.raise_for_status()
        async for linea in r.aiter_lines():
            if not linea.startswith("data:") or linea.strip() == "data: [DONE]":
                continue
            delta = json.loads(linea[5:])["choices"][0].get("delta", {}).get("content") or ""
            pendiente += delta
            partes = _FIN_FRASE.split(pendiente)
            for frase in partes[:-1]:
                if limpia := limpiar_para_voz(frase):
                    yield limpia
            pendiente = partes[-1]
    if limpia := limpiar_para_voz(pendiente):
        yield limpia


async def clips_de_respuesta(frases: AsyncIterator[str]) -> AsyncIterator[str]:
    """Agrupa frases en clips y corta la respuesta al llegar al máximo de palabras."""
    actual: list[str] = []
    palabras_total = 0
    primero = True
    async for frase in frases:
        n = len(frase.split())
        if palabras_total + n > MAX_PALABRAS:
            break
        actual.append(frase)
        palabras_total += n
        objetivo = PALABRAS_PRIMER_CLIP if primero else PALABRAS_CLIP
        if sum(len(f.split()) for f in actual) >= objetivo:
            yield " ".join(actual)
            actual, primero = [], False
    if actual:
        yield " ".join(actual)


async def generar_clip(turno: Turno, indice: int, texto: str) -> None:
    """Un clip: voz + vídeo animado con el backend. Se emite en cuanto está listo."""
    async with CLIPS:
        if turno.cancelado:
            return
        inicio = time.time()
        async with httpx.AsyncClient() as cliente:
            r = await cliente.post(f"{BACKEND_URL}/api/generar", json={
                "texto": texto, "voz": VOZ, "idioma": "es", "modo": "emocional",
                "video": True, "avatar": AVATAR, "escena": ESCENA, "animado": ANIMADO,
            }, timeout=60)
            if r.status_code >= 400:
                raise RuntimeError(f"El backend rechazó el clip {indice}: {r.text[:300]}")
            tarea = r.json()
            turno.tareas_backend.add(tarea["id"])
            try:
                async with cliente.stream("GET", f"{BACKEND_URL}/api/tarea/{tarea['id']}/eventos",
                                          timeout=httpx.Timeout(10, read=None)) as flujo:
                    async for linea in flujo.aiter_lines():
                        if not linea.startswith("data:"):
                            continue
                        ev = json.loads(linea[5:])
                        tipo = ev.get("tipo")
                        if tipo == "fin":
                            video = ev.get("video") or tarea.get("video")
                            if not video:
                                raise RuntimeError("El clip terminó sin vídeo.")
                            log.info("Clip %d listo en %.1f s (%.1f s de audio)", indice,
                                     time.time() - inicio, ev.get("duracion") or 0)
                            await turno.emitir("clip", indice=indice, url=f"/videos/{video}",
                                               texto=texto, duracion=ev.get("duracion"),
                                               segundos=round(time.time() - inicio, 1))
                            return
                        if tipo in ("error", "cancelada"):
                            raise RuntimeError(ev.get("mensaje") or tipo)
                raise RuntimeError("Se cortó la conexión con el backend.")
            finally:
                turno.tareas_backend.discard(tarea["id"])


async def procesar(turno: Turno, wav: bytes | None, texto: str | None = None) -> None:
    inicio = time.time()
    try:
        async with httpx.AsyncClient() as cliente:
            # Sin micrófono, el texto escrito entra como si fuera la transcripción de Whisper
            pregunta = " ".join(texto.split()) if texto else await transcribir(cliente, wav)
            if not pregunta:
                await turno.emitir("vacio")
                return
            # Hay una pregunta real: sustituye a lo que Luisa estuviera respondiendo
            anterior = TURNOS.get(TURNO_ACTIVO.get(turno.sesion, ""))
            TURNO_ACTIVO[turno.sesion] = turno.id
            if anterior and anterior is not turno:
                await _cancelar_turno(anterior)
            await turno.emitir("transcripcion", texto=pregunta,
                               segundos=round(time.time() - inicio, 2))

            fragmentos = await buscar_contexto(cliente, pregunta)
            await turno.emitir("contexto", fuentes=sorted({f["documento"] for f in fragmentos}),
                               segundos=round(time.time() - inicio, 2))

            respuesta: list[str] = []
            indice = 0
            async for clip in clips_de_respuesta(
                    frases_del_llm(cliente, turno.sesion, pregunta, fragmentos)):
                if turno.cancelado:
                    return
                indice += 1
                respuesta.append(clip)
                await turno.emitir("texto", indice=indice, texto=clip,
                                   segundos=round(time.time() - inicio, 2))
                turno.trabajos.append(asyncio.create_task(generar_clip(turno, indice, clip)))
            await turno.emitir("total", clips=indice)

            historial = CONVERSACIONES.setdefault(turno.sesion, [])
            historial += [{"role": "user", "content": pregunta},
                          {"role": "assistant", "content": " ".join(respuesta)}]
            del historial[:-2 * HISTORIAL_TURNOS]
            log.info("P: %s | R (%d clips, %d palabras): %s", pregunta, indice,
                     len(" ".join(respuesta).split()), " ".join(respuesta))

            for resultado in await asyncio.gather(*turno.trabajos, return_exceptions=True):
                if isinstance(resultado, Exception) and not isinstance(
                        resultado, asyncio.CancelledError) and not turno.cancelado:
                    raise resultado
            log.info("Turno %s completo en %.1f s", turno.id, time.time() - inicio)
    except asyncio.CancelledError:
        pass
    except Exception as e:   # cualquier fallo se muestra en la web y el turno termina
        if not turno.cancelado:
            log.exception("Turno %s falló", turno.id)
            await turno.emitir("error", mensaje=str(e) or e.__class__.__name__)
    finally:
        turno.terminado = True
        if TURNO_ACTIVO.get(turno.sesion) == turno.id:
            TURNO_ACTIVO.pop(turno.sesion, None)
        await turno.emitir("fin", cancelado=turno.cancelado)


# ---- Bucle de reposo -----------------------------------------------------------
_reposo_cerrojo = asyncio.Lock()


def _silencio_wav(segundos: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * segundos))
    return buf.getvalue()


async def video_reposo() -> Path | None:
    """Luisa en reposo (parpadea, respira) sobre su fondo: se genera una vez y se reutiliza."""
    registro = DIR_DATOS / f"reposo-{AVATAR}-{ESCENA}.json"
    try:
        nombre = json.loads(registro.read_text())["video"]
        if (DIR_VIDEOS / nombre).is_file():
            return DIR_VIDEOS / nombre
    except (OSError, ValueError, KeyError):
        pass
    async with _reposo_cerrojo:
        async with httpx.AsyncClient() as cliente:
            r = await cliente.post(f"{BACKEND_URL}/api/animaciones", data={
                "motor": "sadtalker", "avatar": AVATAR, "escena": ESCENA, "quieto": "true",
            }, files={"audio": ("silencio.wav", _silencio_wav(SEGUNDOS_REPOSO), "audio/wav")},
                timeout=60)
            if r.status_code >= 400:
                log.warning("No se pudo crear el bucle de reposo: %s", r.text[:300])
                return None
            trabajo = r.json()
            while True:
                await asyncio.sleep(2)
                estado = (await cliente.get(
                    f"{BACKEND_URL}/api/animaciones/{trabajo['id']}", timeout=10)).json()
                if estado["estado"] == "completado":
                    break
                if estado["estado"] in ("fallido", "cancelado"):
                    log.warning("Bucle de reposo %s: %s", estado["estado"], estado.get("error"))
                    return None
        DIR_DATOS.mkdir(parents=True, exist_ok=True)
        registro.write_text(json.dumps({"video": trabajo["video"]}))
        log.info("Bucle de reposo creado: %s", trabajo["video"])
        return DIR_VIDEOS / trabajo["video"]


@app.on_event("startup")
async def preparar_reposo() -> None:
    async def intentar() -> None:
        for _ in range(60):   # espera a que backend y animador estén listos
            try:
                if await video_reposo():
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(30)
    asyncio.create_task(intentar())


# ---- API -------------------------------------------------------------------------
@app.post("/api/turno")
async def crear_turno(audio: UploadFile | None = File(None), texto: str = Form(""),
                      sesion: str = Form("anonima")) -> dict:
    """Un turno a partir del audio del micrófono o, sin micrófono, de `texto`
    (simula lo que habría transcrito el STT)."""
    _purgar()
    wav = await audio.read() if audio else None
    texto = texto.strip()[:2000]
    if not texto and (not wav or len(wav) < 8000):
        raise HTTPException(400, "Falta el audio o el texto")
    turno = Turno(id=uuid.uuid4().hex[:12], sesion=sesion[:64])
    TURNOS[turno.id] = turno
    asyncio.create_task(procesar(turno, wav, texto or None))
    return {"id": turno.id}


@app.get("/api/turno/{id_turno}/eventos")
async def eventos(id_turno: str) -> StreamingResponse:
    turno = TURNOS.get(id_turno)
    if not turno:
        raise HTTPException(404, "El turno no existe")

    async def flujo() -> AsyncIterator[str]:
        while True:
            try:
                ev = await asyncio.wait_for(turno.eventos.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": latido\n\n"
                continue
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev["tipo"] == "fin":
                return

    return StreamingResponse(flujo(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/turno/{id_turno}/cancelar")
async def cancelar(id_turno: str) -> dict:
    if turno := TURNOS.get(id_turno):
        await _cancelar_turno(turno)
    return {"ok": True}


@app.post("/api/sesion/{sesion}/olvidar")
async def olvidar(sesion: str) -> dict:
    CONVERSACIONES.pop(sesion, None)
    return {"ok": True}


@app.get("/api/salud")
async def salud() -> dict:
    estado: dict[str, bool] = {}
    async with httpx.AsyncClient(timeout=3) as cliente:
        for nombre, url in (("stt", f"{STT_URL}/"), ("llm", f"{LLM_URL}/health"),
                            ("backend", f"{BACKEND_URL}/api/salud"),
                            ("ollama", f"{OLLAMA_URL}/api/tags"),
                            ("chroma", f"{CHROMA_URL}/api/v2/heartbeat")):
            try:
                estado[nombre] = (await cliente.get(url)).status_code < 500
            except httpx.HTTPError:
                estado[nombre] = False
    registro = DIR_DATOS / f"reposo-{AVATAR}-{ESCENA}.json"
    return {"ok": all(estado.values()), "servicios": estado, "avatar": AVATAR,
            "escena": ESCENA, "reposo": registro.is_file()}


@app.get("/avatar.png")
async def avatar() -> Response:
    """Imagen fija del avatar sobre su fondo: se ve mientras se carga el bucle de reposo."""
    async with httpx.AsyncClient(timeout=30) as cliente:
        r = await cliente.get(f"{BACKEND_URL}/api/avatares/{AVATAR}/vista/{ESCENA}")
    if r.status_code != 200:
        raise HTTPException(502, "No se pudo obtener la imagen del avatar")
    return Response(r.content, media_type=r.headers.get("content-type", "image/png"),
                    headers={"Cache-Control": "max-age=300"})


@app.api_route("/reposo.mp4", methods=["GET", "HEAD"])
async def reposo() -> FileResponse:
    registro = DIR_DATOS / f"reposo-{AVATAR}-{ESCENA}.json"
    try:
        ruta = DIR_VIDEOS / json.loads(registro.read_text())["video"]
    except (OSError, ValueError, KeyError):
        raise HTTPException(404, "El bucle de reposo aún se está generando")
    if not ruta.is_file():
        raise HTTPException(404, "El bucle de reposo aún se está generando")
    return FileResponse(ruta, media_type="video/mp4")


@app.api_route("/videos/{archivo}", methods=["GET", "HEAD"])
async def video(archivo: str) -> FileResponse:
    ruta = (DIR_VIDEOS / archivo).resolve()
    if ruta.parent != DIR_VIDEOS.resolve() or not ruta.is_file():
        raise HTTPException(404, "Vídeo no encontrado")
    return FileResponse(ruta, media_type="video/mp4")


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web")
