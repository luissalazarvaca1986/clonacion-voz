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
import difflib
import hashlib
import io
import json
import logging
import os
import random
import re
import time
import unicodedata
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import ajustes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("conversa")

# ---- Configuración -----------------------------------------------------------
STT_URL = os.getenv("CONVERSA_STT_URL", "http://clonar-voz-stt:8080")
LLM_URL = os.getenv("CONVERSA_LLM_URL", "http://clonar-voz-llm:8080")
BACKEND_URL = os.getenv("CONVERSA_BACKEND_URL", "http://clonar-voz-backend:8080")
OLLAMA_URL = os.getenv("CONVERSA_OLLAMA_URL", "http://host.docker.internal:11434")
CHROMA_URL = os.getenv("CONVERSA_CHROMA_URL", "http://host.docker.internal:8100")
EMBEDDING = os.getenv("CONVERSA_EMBEDDING", "nomic-embed-text")
# Fondo preferido del avatar (si el avatar no lo tiene, se usa el primero que tenga)
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
DIR_AUDIOS = Path(os.getenv("CONVERSA_DIR_AUDIOS", "/audios"))
DIR_DATOS = Path(os.getenv("CONVERSA_DIR_DATOS", "/datos"))
# Documentos de respaldo cuando SecondBrain (Ollama + ChromaDB) no está en marcha
DIR_CONOCIMIENTO = Path(os.getenv("CONVERSA_DIR_CONOCIMIENTO", "/conocimiento"))
# Turnos previos que ve el LLM: pocos, para que no arrastre el tema anterior
HISTORIAL_TURNOS = int(os.getenv("CONVERSA_HISTORIAL_TURNOS", "2"))
CHROMA_BASE = "/api/v2/tenants/default_tenant/databases/default_database"

# Frases que Whisper «oye» en el silencio o el ruido
_ALUCINACIONES = re.compile(
    r"subt[ií]tulos|amara\.org|suscr[ií]bete|gracias por ver|^\W*$", re.I)
_FIN_FRASE = re.compile(r"(?<=[.!?…])[\"»”)]*\s+")

_SISTEMA = """Eres {nombre}, una asistente virtual que conversa por voz con las personas.
Respondes SIEMPRE en español neutro, con un tono cercano, amable y profesional.

Reglas:
- Basa tu respuesta en la INFORMACIÓN DE REFERENCIA. Si la respuesta no está ahí, dilo con
  naturalidad y ofrece ayuda con otra cosa; nunca inventes datos, precios ni horarios.
- Tu respuesta se convertirá en voz: escribe frases completas y naturales, como si hablaras.
  Nada de listas, viñetas, asteriscos, tablas, emojis, enlaces ni formato markdown.
- Empieza con una frase breve que responda directamente; después amplía si hace falta.
  No abras con fórmulas vacías como «Sí, puedo darte información sobre…» o «Claro, con
  gusto»: la primera frase ya debe contener información.
- Sé breve: entre una y cinco frases suele bastar. Nunca superes {max_palabras} palabras.
- No menciones la «información de referencia», los «documentos» ni que eres un modelo.
- Responde SOLO la PREGUNTA actual. La conversación anterior sirve únicamente para entender
  referencias como «eso» o «y él»; no repitas ni continúes el tema anterior.
- Rattio (Compañía INFORATTIO Cía. Ltda., Quito) no fabrica las plataformas de sus socios:
  las revende, las implementa y presta servicios gestionados sobre ellas. Si preguntan si
  Rattio vende o trabaja con una de esas plataformas, la respuesta es sí, como socio.
- Si solo saludan, agradecen o se despiden, responde con una frase cortés y breve, sin
  hablar de productos.
- Si la pregunta es ambigua («¿ya se corrigió?», «¿y eso?») y no queda claro a qué se
  refiere, pide con amabilidad que la aclare en vez de suponer un tema.
- Si te dan una «Lista completa de productos» y preguntan qué productos o plataformas hay,
  nómbralos TODOS, sin omitir ninguno, con pocas palabras para cada uno.
- Cada fragmento de referencia indica de qué producto habla. Describe cada producto solo con
  sus propios fragmentos y no le atribuyas lo que se dice de otro. Si preguntan por varios
  productos, responde cada uno por separado, empezando por su nombre."""


# ---- Perfil de la sala: avatar y modelos elegidos en «Entrar a la sala» --------
@dataclass(frozen=True)
class Perfil:
    avatar: str
    nombre: str
    voz: str
    escena: str
    video: bool
    llm: str = ""
    tts: str = ""
    animacion: str = ""
    stt: str = ""

    @property
    def simulacion(self) -> bool:
        """«Simulación de movimiento»: el navegador anima la imagen con la voz; al backend
        solo se le pide audio (nada de SadTalker ni de bucle de reposo)."""
        return self.animacion == ajustes.SIMULACION["id"]

    @property
    def video_ia(self) -> bool:
        return self.video and not self.simulacion

    @property
    def clave(self) -> str:
        return f"{self.avatar}-{self.escena}"


PERFIL: Perfil | None = None
STT_CARGADO = ajustes.STT_INICIAL


def perfil_de(config: dict[str, Any], ops: dict[str, Any]) -> Perfil:
    av = next(a for a in ops["avatares"] if a["id"] == config["avatar"])
    preferida = config.get("escena") or ESCENA
    escena = preferida if preferida in av["escenas"] or not av["escenas"] else av["escenas"][0]
    m = config["modelos"]
    return Perfil(avatar=av["id"], nombre=av["nombre"], voz=av["voz"], escena=escena,
                  video=config["video"], llm=m.get("llm", ""), tts=m.get("tts", ""),
                  animacion=m.get("animacion", ""), stt=m.get("stt", ""))


async def perfil_actual() -> Perfil:
    """El de la última vez que se entró a la sala; si no hay, lo primero disponible."""
    global PERFIL
    if PERFIL is None:
        ops = await ajustes.opciones()
        config = ajustes.cargar() or ajustes.por_defecto(ops)
        try:
            PERFIL = perfil_de(ajustes.validar(config, ops), ops)
        except (ValueError, StopIteration):
            PERFIL = perfil_de(ajustes.por_defecto(ops), ops)
    return PERFIL


async def cargar_stt(modelo: str) -> None:
    """Cambia el modelo de whisper-server en caliente (POST /load) si hace falta."""
    global STT_CARGADO
    if not modelo or modelo == STT_CARGADO:
        return
    async with httpx.AsyncClient(timeout=120) as cliente:
        r = await cliente.post(f"{STT_URL}/load",
                               files={"model": (None, f"{ajustes.DIR_STT_REMOTO}/{modelo}")})
    if r.status_code >= 400:
        raise RuntimeError(f"Whisper no pudo cargar {modelo}: {r.text[:200]}")
    STT_CARGADO = modelo
    log.info("STT: cargado %s", modelo)


# ---- Turnos ------------------------------------------------------------------
@dataclass
class Turno:
    id: str
    sesion: str
    perfil: Perfil
    video: bool = True                # False: solo voz, sin animar con SadTalker
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


def _palabras(texto: str) -> set[str]:
    return {p for p in re.findall(r"\w{3,}", texto.lower())}


def contexto_local(pregunta: str) -> list[dict[str, Any]]:
    """Respaldo sin SecondBrain (equipo de desarrollo): secciones de los .md/.txt de
    DIR_CONOCIMIENTO ordenadas por palabras en común con la pregunta."""
    if not DIR_CONOCIMIENTO.is_dir():
        return []
    claves = _palabras(pregunta)
    trozos: list[dict[str, Any]] = []
    for archivo in sorted(DIR_CONOCIMIENTO.glob("*")):
        if archivo.suffix.lower() not in (".md", ".txt") or not archivo.is_file():
            continue
        for seccion in re.split(r"\n(?=#{1,3} )", archivo.read_text(errors="replace")):
            if seccion := seccion.strip():
                comunes = len(claves & _palabras(seccion))
                trozos.append({"texto": seccion, "documento": archivo.name,
                               "distancia": 1 / (1 + comunes)})
    return sorted(trozos, key=lambda t: t["distancia"])[:FRAGMENTOS]


async def buscar_contexto(cliente: httpx.AsyncClient, pregunta: str) -> list[dict[str, Any]]:
    """SecondBrain si está disponible; si no, los documentos locales."""
    try:
        return await buscar_secondbrain(cliente, pregunta)
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        log.warning("SecondBrain no disponible (%s); uso %s", e.__class__.__name__,
                    DIR_CONOCIMIENTO)
        return contexto_local(pregunta)


async def buscar_secondbrain(cliente: httpx.AsyncClient, pregunta: str) -> list[dict[str, Any]]:
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
                json={"query_embeddings": [vector], "n_results": FRAGMENTOS * 3,
                      "include": ["documents", "metadatas", "distances"]}, timeout=15)
            q.raise_for_status()
            return q.json()
        except httpx.HTTPError as e:
            log.warning("Colección %s: %s", col.get("name"), e)
            return None

    # El mismo documento suele estar en varias colecciones (la del producto, «rattio»,
    # «plan_marketing_2027»…): se deduplica por el texto para que lleguen fragmentos distintos
    mejores: dict[str, dict[str, Any]] = {}
    for datos in await asyncio.gather(*(consultar(c) for c in r.json())):
        if not datos:
            continue
        for i, texto in enumerate(datos["documents"][0]):
            distancia = datos["distances"][0][i]
            clave = hashlib.sha1(" ".join(texto.split()).encode()).hexdigest()
            if clave not in mejores or distancia < mejores[clave]["distancia"]:
                meta = datos["metadatas"][0][i] or {}
                mejores[clave] = {"texto": texto, "distancia": distancia,
                                  "documento": meta.get("nombre", "")}
    return elegir_fragmentos(pregunta, sorted(mejores.values(), key=lambda m: m["distancia"]))


def _clave(texto: str) -> str:
    """«Prompt Security» → «promptsecurity»: minúsculas, sin tildes ni espacios."""
    t = unicodedata.normalize("NFD", texto.lower())
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def producto_de(documento: str) -> str:
    """El producto de un documento de SecondBrain: «NinjaOne - SB NinjaOne - …» → «NinjaOne»."""
    return documento.split(" - ")[0].strip() if " - " in documento else ""


def elegir_fragmentos(pregunta: str, candidatos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Si la pregunta nombra productos, solo fragmentos de esos productos, repartidos entre
    ellos (así no se mezclan conceptos de otros); si no nombra ninguno, los más cercanos."""
    q = _clave(pregunta)
    productos = {producto_de(c["documento"]) for c in candidatos} - {""}
    nombrados = sorted((p for p in productos if _clave(p) and _clave(p) in q),
                       key=lambda p: q.index(_clave(p)))
    if not nombrados:
        return candidatos[:FRAGMENTOS]
    cupo = max(2, FRAGMENTOS // len(nombrados))
    elegidos: list[dict[str, Any]] = []
    for p in nombrados:
        elegidos += [c for c in candidatos if producto_de(c["documento"]) == p][:cupo]
    resto = [c for c in candidatos if c not in elegidos and producto_de(c["documento"]) in nombrados]
    elegidos += resto[:max(0, FRAGMENTOS - len(elegidos))]
    return elegidos or candidatos[:FRAGMENTOS]


# ---- Nombres de productos: Whisper los escribe a su manera («Ratio», «Sentinel One»…) ----
_CATALOGO: dict[str, Any] = {"productos": set(), "hora": 0.0}


async def catalogo_productos(cliente: httpx.AsyncClient) -> set[str]:
    """Productos de SecondBrain («NinjaOne - …» → «NinjaOne»), leídos de los nombres de los
    documentos de todas las colecciones; se refresca cada 10 minutos."""
    if time.time() - _CATALOGO["hora"] < 600 and _CATALOGO["productos"]:
        return _CATALOGO["productos"]
    productos: set[str] = set()
    try:
        r = await cliente.get(f"{CHROMA_URL}{CHROMA_BASE}/collections", timeout=10)
        r.raise_for_status()
        for col in r.json():
            q = await cliente.post(f"{CHROMA_URL}{CHROMA_BASE}/collections/{col['id']}/get",
                                   json={"include": ["metadatas"]}, timeout=15)
            if q.status_code == 200:
                for meta in q.json().get("metadatas") or []:
                    if p := producto_de((meta or {}).get("nombre", "")):
                        productos.add(p)
    except httpx.HTTPError as e:
        log.warning("No se pudo leer el catálogo de productos: %s", e)
    if productos:
        _CATALOGO.update(productos=productos, hora=time.time())
    return _CATALOGO["productos"]


def _compacta(texto: str) -> str:
    """«Rattio»/«Ratio», «Sentinel One»/«SentinelOne» → misma clave (sin letras repetidas)."""
    return re.sub(r"(.)\1+", r"\1", _clave(texto))


def corregir_nombres(texto: str, productos: set[str]) -> str:
    """Escribe bien los nombres de producto que Whisper oye parecido. Una sola palabra solo
    se corrige si va en mayúscula («Ratio» sí, «el ratio de…» no)."""
    claves = {_compacta(p): p for p in productos if len(_compacta(p)) >= 4}
    if not claves:
        return texto

    def exacto(nucleo: str) -> str | None:
        return claves.get(_compacta(nucleo))

    def aproximado(nucleo: str) -> str | None:
        # Casi igual («Prom security», «Sentinel Wan»): solo nombres largos, de longitud
        # parecida y con la misma inicial, para no tragarse palabras vecinas («es», «y»)
        clave = _compacta(nucleo)
        if len(clave) < 7:
            return None
        parecidas = [k for k in claves if len(k) >= 7 and k[0] == clave[0]
                     and abs(len(k) - len(clave)) <= 2]
        cerca = difflib.get_close_matches(clave, parecidas, n=1, cutoff=0.86)
        return claves[cerca[0]] if cerca else None

    palabras = texto.split(" ")
    salida: list[str] = []
    i = 0
    while i < len(palabras):
        # Primero coincidencias exactas (3, 2 o 1 palabras); después aproximadas (2 o 1)
        for buscar, tamanos in ((exacto, (3, 2, 1)), (aproximado, (2, 1))):
            hecho = False
            for n in tamanos:
                grupo = palabras[i:i + n]
                if len(grupo) < n:
                    continue
                nucleo = re.sub(r"^\W+|\W+$", "", " ".join(grupo))
                producto = buscar(nucleo) if nucleo else None
                if producto and (n > 1 or nucleo[:1].isupper()):
                    inicio = re.match(r"^\W*", grupo[0]).group()
                    fin = re.search(r"\W*$", grupo[-1]).group()
                    salida.append(f"{inicio}{producto}{fin}")
                    i += n
                    hecho = True
                    break
            if hecho:
                break
        else:
            salida.append(palabras[i])
            i += 1
    return " ".join(salida)


# ---- Preguntas de lista («qué productos tiene Rattio») -----------------------------------
# La tabla de productos de un documento queda repartida en varios fragmentos y la búsqueda
# solo trae algunos, así que la respuesta salía incompleta. Para estas preguntas se arma la
# tabla entera desde el documento y se entrega como un solo fragmento.
_LISTA = re.compile(r"\b(productos?|plataformas?|portafolio|portfolio|soluciones|marcas|"
                    r"fabricantes|socios|cat[aá]logo|qu[eé] (?:venden?|ofrecen?|tienen?|manejan?|"
                    r"comercializan?|distribuyen?))\b", re.I)
_SECCION_LISTA = re.compile(r"#{2,3} (?:Productos|Plataformas)[^#]*", re.I)
_FILA = re.compile(r"\|\s*\*\*([^*|]+)\*\*[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")
_DOCUMENTOS: dict[str, tuple[float, str]] = {}


def _unir_fragmentos(trozos: list[str]) -> str:
    """Une fragmentos consecutivos quitando el solapamiento entre uno y el siguiente."""
    texto = ""
    for trozo in trozos:
        solape = next((n for n in range(min(len(texto), len(trozo), 400), 20, -1)
                       if texto.endswith(trozo[:n])), 0)
        texto += trozo[solape:] if solape else ("\n" + trozo if texto else trozo)
    return texto


async def documento_completo(cliente: httpx.AsyncClient, nombre: str) -> str:
    if (cache := _DOCUMENTOS.get(nombre)) and time.time() - cache[0] < 600:
        return cache[1]
    r = await cliente.get(f"{CHROMA_URL}{CHROMA_BASE}/collections", timeout=10)
    r.raise_for_status()
    for col in r.json():
        q = await cliente.post(f"{CHROMA_URL}{CHROMA_BASE}/collections/{col['id']}/get",
                               json={"include": ["metadatas", "documents"],
                                     "where": {"nombre": nombre}}, timeout=15)
        datos = q.json() if q.status_code == 200 else {}
        if datos.get("ids"):
            trozos = sorted(zip((m.get("chunk_index", 0) for m in datos["metadatas"]),
                                datos["documents"]))
            texto = _unir_fragmentos([t for _, t in trozos])
            _DOCUMENTOS[nombre] = (time.time(), texto)
            return texto
    return ""


_DESCRIPCIONES: dict[str, str] = {}


async def productos_que_faltan(cliente: httpx.AsyncClient, documento: str,
                               lista: str) -> list[str]:
    """Productos del catálogo de SecondBrain que la tabla del documento no nombra (p. ej.
    Valicyber no está en la tabla de la web): se añaden con la primera frase de su propio
    conocimiento que diga qué es."""
    propio = producto_de(documento)
    filas = []
    for p in sorted(await catalogo_productos(cliente)):
        if p == propio or _clave(p) in _clave(lista):
            continue
        if p not in _DESCRIPCIONES:
            descripcion = ""
            try:
                for f in await buscar_secondbrain(cliente, f"¿Qué es {p} y para qué sirve?"):
                    if producto_de(f["documento"]) != p:
                        continue
                    frases = [" ".join(x.split()) for x in re.split(
                        r"(?<=[.!?])\s+|\n+", re.sub(r"[*#|>`]+", " ", f["texto"]))]
                    # La frase que lo define («Valicyber es un…»), nunca una cabecera
                    # interna (fecha, «Uso interno», fuentes con nombres de clientes)
                    utiles = [x for x in frases if _clave(p) in _clave(x) and 40 < len(x) < 300
                              and not re.search(r"uso interno|actualizado|fuente|propuesta|"
                                                r"cliente|reseller", x, re.I)]
                    descripcion = next((x for x in utiles
                                        if re.search(r"\b(es|son)\b", x, re.I)), "")
                    if descripcion:
                        break
            except (httpx.HTTPError, KeyError, IndexError, ValueError):
                pass
            _DESCRIPCIONES[p] = descripcion
        filas.append(f"- {p}: {_DESCRIPCIONES[p]}" if _DESCRIPCIONES[p] else f"- {p}")
    return filas


async def con_lista_completa(cliente: httpx.AsyncClient, consulta: str,
                             fragmentos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Si la pregunta pide la lista de productos y los fragmentos vienen de un documento con
    una tabla de productos, esa tabla va entera y primero."""
    if not _LISTA.search(consulta):
        return fragmentos
    for nombre in dict.fromkeys(f["documento"] for f in fragmentos):
        try:
            texto = await documento_completo(cliente, nombre)
        except httpx.HTTPError:
            continue
        filas = [f"- {n.strip()}: {que.rstrip('.')}. {desc}"
                 for seccion in _SECCION_LISTA.findall(texto)
                 for n, que, desc in _FILA.findall(seccion)
                 if que.strip().lower() not in ("qué es", "---")]
        if len(filas) >= 3:
            filas += await productos_que_faltan(cliente, nombre, "\n".join(filas))
            lista = {"documento": nombre, "distancia": 0.0, "texto":
                     f"Lista completa de productos y plataformas ({len(filas)}):\n"
                     + "\n".join(filas)}
            return [lista] + fragmentos[:max(1, FRAGMENTOS - 3)]
    return fragmentos


TEMA: dict[str, list[str]] = {}   # sesión → últimos productos nombrados


def consulta_de_busqueda(pregunta: str, sesion: str, productos: set[str]) -> str:
    """Una pregunta corta que no nombra ningún producto («¿ya se corrigió?», «¿y cuánto
    cuesta?») se busca con el tema de la conversación (los productos nombrados antes) y la
    pregunta anterior, para no traer fragmentos al azar de otros productos."""
    q = _clave(pregunta)
    nombrados = [p for p in productos if _clave(p) and _clave(p) in q]
    if nombrados:
        TEMA[sesion] = nombrados
        return pregunta
    previas = [m["content"] for m in CONVERSACIONES.get(sesion, []) if m["role"] == "user"]
    if not previas or len(pregunta.split()) > 10:
        return pregunta
    tema = " ".join(TEMA.get(sesion, []))
    return " ".join(x for x in (tema, previas[-1], pregunta) if x)


def limpiar_para_voz(texto: str) -> str:
    texto = re.sub(r"<think>.*?</think>", "", texto, flags=re.S)
    texto = re.sub(r"[*_#`>|~]+", "", texto)
    texto = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", texto, flags=re.M)
    texto = re.sub(r"https?://\S+", "", texto)
    texto = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", texto)
    return " ".join(texto.split())


async def frases_del_llm(cliente: httpx.AsyncClient, perfil: Perfil, sesion: str, pregunta: str,
                         fragmentos: list[dict[str, Any]]) -> AsyncIterator[str]:
    """Respuesta del LLM en streaming, entregada frase a frase en cuanto se completa cada una."""
    referencia = "\n\n".join(
        f"[Producto: {producto_de(f['documento']) or 'general'} · {f['documento']}]\n{f['texto']}"
        for f in fragmentos) or "(no hay información disponible)"
    mensajes = [{"role": "system", "content": _SISTEMA.format(nombre=perfil.nombre,
                                                              max_palabras=MAX_PALABRAS)}]
    # Una pregunta que nombra un producto se entiende sola: sin historial, para que el LLM no
    # siga con el tema anterior (el historial queda para «¿y cuánto cuesta?», «¿y eso?»…)
    q = _clave(pregunta)
    autonoma = any(_clave(producto_de(f["documento"])) in q
                   for f in fragmentos if producto_de(f["documento"]))
    if not autonoma:
        mensajes += CONVERSACIONES.get(sesion, [])[-2 * HISTORIAL_TURNOS:]
    mensajes.append({"role": "user", "content":
                     f"INFORMACIÓN DE REFERENCIA:\n{referencia}\n\nPREGUNTA: {pregunta}"})
    pendiente = ""
    async with cliente.stream("POST", f"{LLM_URL}/v1/chat/completions", json={
        # «model» elige el LLM en llama-server modo router; con un solo modelo se ignora
        **({"model": perfil.llm} if perfil.llm else {}),
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


# ---- Pronunciación: nombres en inglés que la voz lee a la española ---------------
# Solo cambia el texto que se envía a la voz; en pantalla se sigue viendo bien escrito.
# Probado con ida y vuelta (voz → Whisper): «Dynatrace» se oía «Dina Traes», «Dáinatreis»
# se oye «Dynatrace». Se pueden añadir o cambiar en DIR_DATOS/pronunciacion.json.
PRONUNCIACION = {
    "Dynatrace": "Dáinatreis",
    "Valicyber": "Vali Sáiber",
    "ValiCyber": "Vali Sáiber",
    "Ironchip": "Áiron chip",
}


def _pronunciacion() -> dict[str, str]:
    reglas = dict(PRONUNCIACION)
    try:
        reglas.update(json.loads((DIR_DATOS / "pronunciacion.json").read_text("utf-8")))
    except (OSError, ValueError):
        pass
    return reglas


def para_voz(texto: str) -> str:
    for palabra, dicha in _pronunciacion().items():
        texto = re.sub(rf"\b{re.escape(palabra)}\b", dicha, texto, flags=re.I)
    return texto


async def sintetizar(cliente: httpx.AsyncClient, texto: str, video: bool, perfil: Perfil,
                     tareas: set[str] | None = None) -> dict[str, Any]:
    """Voz (+ vídeo animado de Luisa si `video`) con el backend. Devuelve el evento «fin»
    con la URL pública del clip en `url`."""
    r = await cliente.post(f"{BACKEND_URL}/api/generar", json={
        "texto": texto, "voz": perfil.voz, "idioma": "es", "modo": "emocional",
        **({"modelo_tts": perfil.tts} if perfil.tts else {}),
        **({"video": True, "avatar": perfil.avatar, "escena": perfil.escena, "animado": ANIMADO,
            **({"motor_animacion": perfil.animacion} if perfil.animacion else {})}
           if video else {"video": False}),
    }, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"El backend rechazó el clip: {r.text[:300]}")
    tarea = r.json()
    if tareas is not None:
        tareas.add(tarea["id"])
    try:
        async with cliente.stream("GET", f"{BACKEND_URL}/api/tarea/{tarea['id']}/eventos",
                                  timeout=httpx.Timeout(10, read=None)) as flujo:
            async for linea in flujo.aiter_lines():
                if not linea.startswith("data:"):
                    continue
                ev = json.loads(linea[5:])
                tipo = ev.get("tipo")
                if tipo == "fin":
                    if video:
                        archivo = ev.get("video") or tarea.get("video")
                        if not archivo:
                            raise RuntimeError("El clip terminó sin vídeo.")
                        ev["url"] = f"/videos/{archivo}"
                    else:
                        if not ev.get("archivo"):
                            raise RuntimeError("El clip terminó sin audio.")
                        ev["url"] = f"/audios/{ev['archivo']}"
                    return ev
                if tipo in ("error", "cancelada"):
                    raise RuntimeError(ev.get("mensaje") or tipo)
        raise RuntimeError("Se cortó la conexión con el backend.")
    finally:
        if tareas is not None:
            tareas.discard(tarea["id"])


async def generar_clip(turno: Turno, indice: int, texto: str) -> None:
    """Un clip de la respuesta. Se emite en cuanto está listo."""
    async with CLIPS:
        if turno.cancelado:
            return
        inicio = time.time()
        async with httpx.AsyncClient() as cliente:
            ev = await sintetizar(cliente, para_voz(texto), turno.video, turno.perfil,
                                  turno.tareas_backend)
        log.info("Clip %d listo en %.1f s (%.1f s de audio)", indice,
                 time.time() - inicio, ev.get("duracion") or 0)
        await turno.emitir("clip", indice=indice, url=ev["url"],
                           medio="video" if turno.video else "audio", texto=texto,
                           duracion=ev.get("duracion"), segundos=round(time.time() - inicio, 1))


# ---- Relleno: «Déjame pensarlo…» mientras se prepara la respuesta --------------
# Varias frases para que no suene monótono: en cada pregunta se sortea una entre las que
# ya tienen audio, sin repetir la última de esa sesión. Se pueden cambiar con
# CONVERSA_TEXTOS_RELLENO (frases separadas por «|»).
TEXTOS_RELLENO = [t.strip() for t in os.getenv("CONVERSA_TEXTOS_RELLENO", "|".join([
    "Déjame pensarlo, te respondo en breve.",
    "Buena pregunta, dame un momento.",
    "Un segundo, reviso la información.",
    "Déjame consultarlo y te cuento.",
    "Permíteme un momento, lo busco.",
    "Interesante, déjame revisar.",
    "Dame un instante, por favor.",
    "Lo reviso y te respondo enseguida.",
    "Muy bien, déjame ver qué tengo sobre eso.",
    "Perfecto, un momento mientras lo consulto.",
    "Entendido, dame unos segundos.",
    "Claro, ya lo busco.",
])).split("|") if t.strip()]
RELLENO = os.getenv("CONVERSA_RELLENO", "1") == "1"
_relleno_cerrojo = asyncio.Lock()
ULTIMO_RELLENO: dict[str, str] = {}   # sesión → última frase usada


def _registro_relleno(perfil: Perfil, video: bool, texto: str) -> Path:
    clave = hashlib.sha1(f"{texto}|{perfil.voz}|{perfil.avatar}|{perfil.escena}|"
                         f"{perfil.tts}".encode()).hexdigest()[:10]
    return DIR_DATOS / f"relleno-{'video' if video else 'audio'}-{clave}.json"


def _url_relleno(perfil: Perfil, video: bool, texto: str) -> str | None:
    """URL del relleno de esa frase ya generado, o None si aún no existe (o se borró)."""
    try:
        url = json.loads(_registro_relleno(perfil, video, texto).read_text())["url"]
    except (OSError, ValueError, KeyError):
        return None
    carpeta = DIR_VIDEOS if url.startswith("/videos/") else DIR_AUDIOS
    return url if (carpeta / Path(url).name).is_file() else None


def elegir_relleno(perfil: Perfil, video: bool, sesion: str) -> tuple[str, str] | None:
    """Sortea una frase de las que ya tienen audio, sin repetir la última de la sesión."""
    if not RELLENO:
        return None
    listos = [(t, u) for t in TEXTOS_RELLENO if (u := _url_relleno(perfil, video, t))]
    if not listos:
        return None
    candidatos = [x for x in listos if x[0] != ULTIMO_RELLENO.get(sesion)] or listos
    texto, url = random.choice(candidatos)
    ULTIMO_RELLENO[sesion] = texto
    return texto, url


async def crear_relleno(perfil: Perfil, video: bool) -> bool:
    """Genera el audio (o vídeo) de las frases que aún no lo tienen, una tras otra."""
    async with _relleno_cerrojo:
        for texto in TEXTOS_RELLENO:
            if _url_relleno(perfil, video, texto):
                continue
            async with httpx.AsyncClient() as cliente:
                ev = await sintetizar(cliente, para_voz(texto), video, perfil)
            DIR_DATOS.mkdir(parents=True, exist_ok=True)
            _registro_relleno(perfil, video, texto).write_text(json.dumps({"url": ev["url"]}))
            log.info("Relleno (%s) creado: «%s» → %s", "vídeo" if video else "audio",
                     texto, ev["url"])
        return True


async def procesar(turno: Turno, wav: bytes | None, texto: str | None = None) -> None:
    inicio = time.time()
    try:
        async with httpx.AsyncClient() as cliente:
            # Sin micrófono, el texto escrito entra como si fuera la transcripción de Whisper
            pregunta = " ".join(texto.split()) if texto else await transcribir(cliente, wav)
            pregunta = corregir_nombres(pregunta, await catalogo_productos(cliente))
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
            if relleno := elegir_relleno(turno.perfil, turno.video, turno.sesion):
                await turno.emitir("relleno", url=relleno[1], texto=relleno[0])

            productos = await catalogo_productos(cliente)
            consulta = consulta_de_busqueda(pregunta, turno.sesion, productos)
            fragmentos = await con_lista_completa(
                cliente, consulta, await buscar_contexto(cliente, consulta))
            await turno.emitir("contexto", fuentes=sorted({f["documento"] for f in fragmentos}),
                               segundos=round(time.time() - inicio, 2))

            respuesta: list[str] = []
            indice = 0
            async for clip in clips_de_respuesta(
                    frases_del_llm(cliente, turno.perfil, turno.sesion, pregunta, fragmentos)):
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
_reposo_cerrojo: dict[str, asyncio.Lock] = {}


def _silencio_wav(segundos: float) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * segundos))
    return buf.getvalue()


def _registro_reposo(perfil: Perfil) -> Path:
    return DIR_DATOS / f"reposo-{perfil.clave}.json"


def ruta_reposo(perfil: Perfil) -> Path | None:
    try:
        ruta = DIR_VIDEOS / json.loads(_registro_reposo(perfil).read_text())["video"]
    except (OSError, ValueError, KeyError):
        return None
    return ruta if ruta.is_file() else None


async def video_reposo(perfil: Perfil) -> Path | None:
    """El avatar en reposo (parpadea, respira) sobre su fondo: se genera una vez y se reutiliza."""
    registro = _registro_reposo(perfil)
    try:
        nombre = json.loads(registro.read_text())["video"]
        if (DIR_VIDEOS / nombre).is_file():
            return DIR_VIDEOS / nombre
    except (OSError, ValueError, KeyError):
        pass
    async with _reposo_cerrojo.setdefault(perfil.clave, asyncio.Lock()):
        if ruta := ruta_reposo(perfil):      # otro lo generó mientras esperaba
            return ruta
        async with httpx.AsyncClient() as cliente:
            r = await cliente.post(f"{BACKEND_URL}/api/animaciones", data={
                "motor": perfil.animacion or "sadtalker", "avatar": perfil.avatar,
                "escena": perfil.escena, "quieto": "true",
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


# ---- Preparación de la sala: bucle de reposo y relleno del perfil elegido ---------
PREPARANDO: dict[str, asyncio.Task] = {}


def preparar(perfil: Perfil) -> None:
    """Genera en segundo plano lo que la sala necesita y aún no existe (una sola vez)."""
    async def intentar(nombre: str, crear) -> None:
        for _ in range(60):   # espera a que backend y animador estén listos
            try:
                if await crear():
                    return
            except (httpx.HTTPError, RuntimeError) as e:
                log.warning("%s pendiente: %s", nombre, e)
            await asyncio.sleep(30)

    trabajos = {}
    if perfil.video_ia:   # el bucle de reposo solo se ve con el vídeo de IA
        trabajos[f"reposo|{perfil.clave}|{perfil.animacion}"] = lambda: video_reposo(perfil)
    if RELLENO:   # el de audio primero: tarda segundos; el de vídeo solo con vídeo de IA
        trabajos[f"relleno-audio|{perfil}"] = lambda: crear_relleno(perfil, False)
        if perfil.video_ia:
            trabajos[f"relleno-video|{perfil}"] = lambda: crear_relleno(perfil, True)
    for clave, crear in trabajos.items():
        if clave not in PREPARANDO or PREPARANDO[clave].done():
            PREPARANDO[clave] = asyncio.create_task(intentar(clave.split("|")[0], crear))


@app.on_event("startup")
async def arrancar() -> None:
    """Deja lista la sala de la última vez (o la de lo primero disponible)."""
    async def tarea() -> None:
        for _ in range(60):
            try:
                perfil = await perfil_actual()
                await cargar_stt(perfil.stt)
                preparar(perfil)
                return
            except (httpx.HTTPError, RuntimeError, KeyError, StopIteration) as e:
                log.warning("Sala pendiente: %s", e)
                global PERFIL
                PERFIL = None
            await asyncio.sleep(20)
    asyncio.create_task(tarea())


# ---- API -------------------------------------------------------------------------
@app.get("/api/opciones")
async def ver_opciones() -> dict:
    """Avatares y modelos disponibles en este equipo, más la configuración guardada
    (`guardada` es null la primera vez: la web propone lo primero de cada lista)."""
    ops = await ajustes.opciones()
    guardada = ajustes.cargar()
    return {**ops, "guardada": guardada, "propuesta": guardada or ajustes.por_defecto(ops)}


@app.put("/api/configuracion")
async def guardar_configuracion(datos: dict[str, Any]) -> dict:
    """«Entrar a la sala»: valida la configuración, la guarda en SQLite (será la de
    defecto la próxima vez), aplica los modelos y prepara la sala."""
    global PERFIL
    ops = await ajustes.opciones()
    try:
        config = ajustes.validar(datos, ops)
    except ValueError as e:
        raise HTTPException(400, str(e))
    perfil = perfil_de(config, ops)
    try:
        await cargar_stt(perfil.stt)
    except (httpx.HTTPError, RuntimeError) as e:
        raise HTTPException(502, f"No se pudo cargar el modelo de voz a texto: {e}")
    ajustes.guardar(config)
    if PERFIL and PERFIL.avatar != perfil.avatar:
        CONVERSACIONES.clear()          # otro avatar: conversación nueva
        TEMA.clear()
    PERFIL = perfil
    preparar(perfil)
    return {"configuracion": config,
            "reposo": perfil.simulacion or ruta_reposo(perfil) is not None,
            "nombre": perfil.nombre}


@app.post("/api/transcribir")
async def solo_transcribir(audio: UploadFile = File(...)) -> dict:
    """Modo micrófono: cada frase detectada se transcribe y la web la añade al campo de
    texto; el envío lo decide la web (silencio prolongado o Intro)."""
    wav = await audio.read()
    if len(wav) < 8000:
        return {"texto": ""}
    async with httpx.AsyncClient() as cliente:
        try:
            return {"texto": corregir_nombres(await transcribir(cliente, wav),
                                              await catalogo_productos(cliente))}
        except httpx.HTTPError as e:
            raise HTTPException(502, f"No se pudo transcribir: {e}")


@app.post("/api/turno")
async def crear_turno(audio: UploadFile | None = File(None), texto: str = Form(""),
                      sesion: str = Form("anonima"), medio: str = Form("")) -> dict:
    """Un turno a partir del audio del micrófono o, sin micrófono, de `texto`
    (simula lo que habría transcrito el STT). `medio` = «video» (voz + avatar animado)
    o «audio» (solo voz sobre el bucle de reposo, mucho más rápido)."""
    _purgar()
    wav = await audio.read() if audio else None
    texto = texto.strip()[:2000]
    if not texto and (not wav or len(wav) < 8000):
        raise HTTPException(400, "Falta el audio o el texto")
    perfil = await perfil_actual()
    # Con la simulación de movimiento el vídeo lo hace el navegador: al backend, solo voz
    video = (perfil.video if medio not in ("video", "audio") else medio == "video") \
        and not perfil.simulacion
    turno = Turno(id=uuid.uuid4().hex[:12], sesion=sesion[:64], perfil=perfil, video=video)
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
    TEMA.pop(sesion, None)
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
    secondbrain = estado["ollama"] and estado["chroma"]
    esenciales = [v for k, v in estado.items() if k not in ("ollama", "chroma")]
    perfil = PERFIL
    return {"ok": all(esenciales) and (secondbrain or DIR_CONOCIMIENTO.is_dir()),
            "servicios": estado, "conocimiento": "secondbrain" if secondbrain else "local",
            "perfil": perfil.__dict__ if perfil else None,
            "reposo": bool(perfil and ruta_reposo(perfil))}


@app.get("/avatar.png")
async def avatar(avatar: str = "", escena: str = "") -> Response:
    """Imagen fija del avatar sobre su fondo: vista previa en la pantalla de entrada y
    fondo de la sala mientras se carga el bucle de reposo."""
    perfil = await perfil_actual()
    id_avatar = avatar or perfil.avatar
    escena = escena or perfil.escena
    if not re.fullmatch(r"[\w .-]{1,80}", id_avatar) or not re.fullmatch(r"\w{1,30}", escena):
        raise HTTPException(400, "Avatar o escena no válidos")
    async with httpx.AsyncClient(timeout=30) as cliente:
        r = await cliente.get(f"{BACKEND_URL}/api/avatares/{id_avatar}/vista/{escena}")
        if r.status_code == 400:   # ese avatar no tiene la escena preferida: su primera
            r = await cliente.get(f"{BACKEND_URL}/api/avatares/{id_avatar}/vista/dia")
    if r.status_code != 200:
        raise HTTPException(502, "No se pudo obtener la imagen del avatar")
    # El backend da un PNG de ~2 MB; en el celular por Wi-Fi tarda y a veces se corta a
    # medias. Se envía como JPEG (mismo tamaño en píxeles, ~10 veces más liviano).
    contenido = await asyncio.to_thread(_a_jpeg, r.content)
    return Response(contenido, media_type="image/jpeg", headers={"Cache-Control": "max-age=300"})


_JPEG_CACHE: dict[str, bytes] = {}


def _a_jpeg(png: bytes) -> bytes:
    clave = hashlib.sha1(png).hexdigest()
    if clave not in _JPEG_CACHE:
        from PIL import Image
        salida = io.BytesIO()
        Image.open(io.BytesIO(png)).convert("RGB").save(salida, "JPEG", quality=85,
                                                         optimize=True, progressive=True)
        if len(_JPEG_CACHE) > 32:
            _JPEG_CACHE.clear()
        _JPEG_CACHE[clave] = salida.getvalue()
    return _JPEG_CACHE[clave]


@app.get("/rostro.json")
async def ver_rostro(avatar: str) -> dict:
    """Puntos de la cara del avatar para la «Simulación de movimiento»."""
    if not re.fullmatch(r"[\w .-]{1,80}", avatar):
        raise HTTPException(400, "Avatar no válido")
    datos = ajustes.rostro(avatar)
    if datos is None:
        raise HTTPException(404, "Ese avatar no tiene puntos del rostro")
    return datos


@app.api_route("/reposo.mp4", methods=["GET", "HEAD"])
async def reposo() -> FileResponse:
    ruta = ruta_reposo(await perfil_actual())
    if ruta is None:
        raise HTTPException(404, "El bucle de reposo aún se está generando")
    return FileResponse(ruta, media_type="video/mp4", headers={"Cache-Control": "no-cache"})


@app.api_route("/videos/{archivo}", methods=["GET", "HEAD"])
async def video(archivo: str) -> FileResponse:
    ruta = (DIR_VIDEOS / archivo).resolve()
    if ruta.parent != DIR_VIDEOS.resolve() or not ruta.is_file():
        raise HTTPException(404, "Vídeo no encontrado")
    return FileResponse(ruta, media_type="video/mp4")


@app.api_route("/audios/{archivo}", methods=["GET", "HEAD"])
async def audio_clip(archivo: str) -> FileResponse:
    ruta = DIR_AUDIOS / Path(archivo).name
    if ruta.suffix != ".wav" or not ruta.is_file():
        raise HTTPException(404, "No existe ese audio")
    return FileResponse(ruta, media_type="audio/wav")


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web")

