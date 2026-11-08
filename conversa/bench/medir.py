#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mide el rendimiento de cada etapa del pipeline de conversación.

    python3 conversa/bench/medir.py stt     # Whisper: tiempo y factor de tiempo real
    python3 conversa/bench/medir.py llm     # Qwen3.5: primer token y tokens/s
    python3 conversa/bench/medir.py tts     # Qwen3-TTS: segundos de cómputo por segundo de audio
    python3 conversa/bench/medir.py turno   # turno completo por texto: hasta el primer clip y total

Usa los puertos del host: backend 8093, conversa 9090; STT y LLM por la red de Docker
(se pasan con --stt / --llm si se exponen).
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request

BACKEND = "http://localhost:8093"
CONVERSA = "http://localhost:9090"


def _post(url: str, datos: dict, timeout: float = 600) -> dict:
    r = urllib.request.Request(url, json.dumps(datos).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=timeout))


def _sse(url: str):
    with urllib.request.urlopen(url, timeout=3600) as r:
        for linea in r:
            linea = linea.decode().strip()
            if linea.startswith("data:"):
                yield json.loads(linea[5:])


def medir_llm(url: str, n: int) -> None:
    pregunta = "¿A qué hora abren los sábados y hacen envíos a domicilio?"
    primeros, velocidades = [], []
    for _ in range(n):
        inicio = time.time()
        primero = None
        tokens = 0
        r = urllib.request.Request(f"{url}/v1/chat/completions", json.dumps({
            "messages": [{"role": "user", "content": pregunta}], "stream": True,
            "max_tokens": 300, "chat_template_kwargs": {"enable_thinking": False},
        }).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=120) as resp:
            for linea in resp:
                linea = linea.decode().strip()
                if not linea.startswith("data:") or linea.endswith("[DONE]"):
                    continue
                if json.loads(linea[5:])["choices"][0].get("delta", {}).get("content"):
                    primero = primero or time.time() - inicio
                    tokens += 1
        total = time.time() - inicio
        primeros.append(primero or total)
        velocidades.append(tokens / max(1e-6, total - (primero or 0)))
    print(f"LLM  primer token: {statistics.median(primeros)*1000:.0f} ms · "
          f"{statistics.median(velocidades):.0f} tokens/s (mediana de {n})")


def medir_tts(n: int, voz: str) -> None:
    texto = ("Hola, soy Luisa. Abrimos de lunes a viernes de nueve de la mañana a siete de la "
             "tarde, y los sábados de nueve a dos.")
    for i in range(n):
        inicio = time.time()
        tarea = _post(f"{BACKEND}/api/generar", {"texto": texto, "voz": voz, "idioma": "es"})
        fin = next(ev for ev in _sse(f"{BACKEND}/api/tarea/{tarea['id']}/eventos")
                   if ev["tipo"] in ("fin", "error", "cancelada"))
        total = time.time() - inicio
        if fin["tipo"] != "fin":
            print("TTS  error:", fin)
            return
        print(f"TTS  #{i+1}: {total:.1f} s para {fin['duracion']:.1f} s de audio "
              f"(factor {total / fin['duracion']:.2f}×)")


def medir_turno(texto: str) -> None:
    inicio = time.time()
    r = urllib.request.Request(f"{CONVERSA}/api/turno",
                               data=f"texto={urllib.parse.quote(texto)}&sesion=bench".encode(),
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
    turno = json.load(urllib.request.urlopen(r, timeout=30))
    audio = 0.0
    for ev in _sse(f"{CONVERSA}/api/turno/{turno['id']}/eventos"):
        t = time.time() - inicio
        if ev["tipo"] == "texto":
            print(f"  {t:6.1f} s  texto clip {ev['indice']}: {ev['texto'][:70]}")
        elif ev["tipo"] == "clip":
            audio += ev.get("duracion") or 0
            print(f"  {t:6.1f} s  CLIP {ev['indice']} listo ({ev.get('duracion') or 0:.1f} s de vídeo, "
                  f"{ev['segundos']} s de generación)")
        elif ev["tipo"] in ("contexto", "total", "error"):
            print(f"  {t:6.1f} s  {ev['tipo']}: {json.dumps(ev, ensure_ascii=False)[:120]}")
        elif ev["tipo"] == "fin":
            print(f"TURNO total {t:.1f} s para {audio:.1f} s de respuesta")
            return


if __name__ == "__main__":
    import urllib.parse
    ap = argparse.ArgumentParser()
    ap.add_argument("que", choices=["llm", "tts", "turno"])
    ap.add_argument("--llm", default="http://192.168.93.17:8080")
    ap.add_argument("--voz", default="447d4a974c1b")
    ap.add_argument("-n", type=int, default=3)
    ap.add_argument("--texto", default="Hola Luisa, ¿a qué hora abren los sábados y cuánto cuesta el envío?")
    a = ap.parse_args()
    {"llm": lambda: medir_llm(a.llm, a.n), "tts": lambda: medir_tts(a.n, a.voz),
     "turno": lambda: medir_turno(a.texto)}[a.que]()
