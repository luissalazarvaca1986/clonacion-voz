# -*- coding: utf-8 -*-
"""Configuración de la sala (pantalla previa «Entrar a la sala»), guardada en SQLite.

- `opciones()` reúne lo que hay disponible en ESTE equipo: avatares (backend), modelos de
  STT (carpeta de Whisper), LLM (los que sirve llama-server), TTS (backend) y motores de
  animación (backend). Nada está fijado en el código: el equipo de desarrollo (GTX 1650)
  y el de producción (DGX Spark) ofrecen sus propios modelos con el mismo código.
- `validar()` comprueba una configuración contra esas opciones.
- La primera vez no hay nada guardado: la web elige lo primero disponible de cada lista
  y lo guarda al pulsar «Entrar a la sala». Desde entonces es la configuración por defecto.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

import httpx

BACKEND_URL = os.getenv("CONVERSA_BACKEND_URL", "http://clonar-voz-backend:8080")
LLM_URL = os.getenv("CONVERSA_LLM_URL", "http://clonar-voz-llm:8080")
# Carpeta de modelos de Whisper vista desde aquí (solo lectura) y desde el contenedor STT
DIR_STT = Path(os.getenv("CONVERSA_DIR_STT", "/modelos-stt"))
DIR_STT_REMOTO = os.getenv("CONVERSA_DIR_STT_REMOTO", "/modelos")
STT_INICIAL = os.getenv("CONVERSA_STT_MODELO", "")      # el que carga whisper-server al arrancar
BD = Path(os.getenv("CONVERSA_BD", "/datos/conversa.sqlite3"))

FUNCIONES = ("stt", "llm", "tts", "animacion")
# Fondo preferido la primera vez (si el avatar lo tiene)
ESCENA_INICIAL = os.getenv("CONVERSA_ESCENA", "noche")
_cerrojo = threading.Lock()


# ---- SQLite ------------------------------------------------------------------
def _conexion() -> sqlite3.Connection:
    BD.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BD)
    con.execute("""CREATE TABLE IF NOT EXISTS configuracion (
                       id INTEGER PRIMARY KEY CHECK (id = 1),
                       datos TEXT NOT NULL,
                       actualizado REAL NOT NULL)""")
    return con


def cargar() -> dict[str, Any] | None:
    """La configuración guardada, o None si nunca se ha entrado a la sala."""
    with _cerrojo, _conexion() as con:
        fila = con.execute("SELECT datos FROM configuracion WHERE id = 1").fetchone()
    return json.loads(fila[0]) if fila else None


def guardar(datos: dict[str, Any]) -> None:
    with _cerrojo, _conexion() as con:
        con.execute("INSERT INTO configuracion (id, datos, actualizado) VALUES (1, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET datos = excluded.datos, "
                    "actualizado = excluded.actualizado",
                    (json.dumps(datos, ensure_ascii=False), time.time()))


# ---- Opciones disponibles en este equipo ------------------------------------
def _nombre(archivo: str) -> str:
    """«Qwen3.5-2B-Q4_K_M.gguf» → «Qwen3.5-2B-Q4_K_M»; «ggml-small.bin» → «small»."""
    base = archivo.rsplit(".", 1)[0] if archivo.endswith((".gguf", ".bin")) else archivo
    return base.removeprefix("ggml-")


async def _json(cliente: httpx.AsyncClient, url: str) -> Any:
    try:
        r = await cliente.get(url, timeout=8)
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, ValueError):
        return None


async def opciones() -> dict[str, Any]:
    async with httpx.AsyncClient() as cliente:
        avatares = await _json(cliente, f"{BACKEND_URL}/api/avatares") or []
        voces = {v["id"] for v in await _json(cliente, f"{BACKEND_URL}/api/voces") or []}
        tts = await _json(cliente, f"{BACKEND_URL}/api/modelo/disponibles") or []
        motores = await _json(cliente, f"{BACKEND_URL}/api/animaciones/motores") or []
        llm = (await _json(cliente, f"{LLM_URL}/v1/models") or {}).get("data") or []

    stt = sorted(p.name for p in DIR_STT.glob("*.bin")) if DIR_STT.is_dir() else []
    if not stt and STT_INICIAL:
        stt = [STT_INICIAL]
    # El modelo con el que arrancó whisper-server va primero: es el que ya está cargado
    if STT_INICIAL in stt:
        stt.remove(STT_INICIAL)
        stt.insert(0, STT_INICIAL)
    # En el TTS va primero el que el backend usa por defecto en este equipo
    tts = sorted(tts, key=lambda m: not m.get("actual"))
    # En el LLM, el que ya está cargado
    llm = sorted(llm, key=lambda m: (m.get("status") or {}).get("value") != "loaded")

    return {
        # Utilizables: completos y con su voz en este equipo (van primero)
        "avatares": [{"id": a["id"], "nombre": a.get("nombre") or a["id"], "voz": a.get("voz"),
                      "escenas": a.get("escenas") or [], "imagen": f"/avatar.png?avatar={a['id']}"}
                     for a in avatares if a.get("voz") in voces and a.get("completo", True)],
        # Los demás se muestran deshabilitados, con el motivo
        "no_disponibles": [{"id": a["id"], "nombre": a.get("nombre") or a["id"],
                            "motivo": "sin voz en este equipo" if a.get("voz") not in voces
                            else "avatar incompleto"}
                           for a in avatares if a.get("voz") not in voces or not a.get("completo", True)],
        "modelos": {
            "stt": [{"id": m, "nombre": _nombre(m)} for m in stt],
            "llm": [{"id": m["id"], "nombre": _nombre(m["id"])} for m in llm],
            "tts": [{"id": m["id"], "nombre": _nombre(m["id"])} for m in tts],
            "animacion": [{"id": m["id"], "nombre": m.get("nombre") or m["id"]}
                          for m in motores if m.get("disponible")],
        },
    }


def por_defecto(ops: dict[str, Any]) -> dict[str, Any]:
    """Primera vez: lo primero disponible de cada lista."""
    primero = lambda lista: lista[0]["id"] if lista else ""   # noqa: E731
    escenas = ops["avatares"][0]["escenas"] if ops["avatares"] else []
    return {
        "avatar": primero(ops["avatares"]),
        "escena": ESCENA_INICIAL if ESCENA_INICIAL in escenas or not escenas else escenas[0],
        # Vídeo animado por defecto según el equipo (CONVERSA_VIDEO): lento en la GTX 1650
        "video": os.getenv("CONVERSA_VIDEO", "0") == "1",
        "microfono": True,
        "modelos": {f: primero(ops["modelos"][f]) for f in FUNCIONES},
        "salida": {"id": "default", "nombre": "Predeterminado del sistema"},
        "reduccion_ruido": False,
    }


def validar(datos: dict[str, Any], ops: dict[str, Any]) -> dict[str, Any]:
    """Normaliza la configuración enviada por la web; ValueError si algo no existe aquí."""
    avatar = next((a for a in ops["avatares"] if a["id"] == datos.get("avatar")), None)
    if avatar is None:
        raise ValueError(f"El avatar «{datos.get('avatar')}» no está disponible.")
    escena = datos.get("escena") or ESCENA_INICIAL
    if avatar["escenas"] and escena not in avatar["escenas"]:
        escena = ESCENA_INICIAL if ESCENA_INICIAL in avatar["escenas"] else avatar["escenas"][0]
    modelos = {}
    for funcion in FUNCIONES:
        valor = (datos.get("modelos") or {}).get(funcion) or ""
        disponibles = {m["id"] for m in ops["modelos"][funcion]}
        if disponibles and valor not in disponibles:
            raise ValueError(f"El modelo «{valor}» ({funcion}) no está disponible.")
        modelos[funcion] = valor
    salida = datos.get("salida") or {}
    return {
        "avatar": datos["avatar"],
        "escena": escena,
        "video": bool(datos.get("video")),
        "microfono": bool(datos.get("microfono", True)),
        "modelos": modelos,
        "salida": {"id": str(salida.get("id") or "default")[:200],
                   "nombre": str(salida.get("nombre") or "")[:200]},
        "reduccion_ruido": bool(datos.get("reduccion_ruido")),
    }
