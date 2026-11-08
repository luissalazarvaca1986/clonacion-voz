# -*- coding: utf-8 -*-
"""Simulador del pipeline para probar la web sin modelos.

Misma API que app.py, pero los clips son vídeos de prueba (borde de color + tono)
que «llegan» con tiempos realistas: el 1 pronto, el 2 antes de que acabe el 1, el 3
tarde (Luisa debe volver al reposo sin cortes mientras espera) y el 4 enseguida.

    docker run --rm -p 9090:8080 -v $PWD/conversa:/app/conversa:ro \
        -v <dir con clipN.mp4, reposo.mp4, avatar.png>:/sim:ro \
        clonar-voz-conversa:latest uvicorn conversa.bench.simulador:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

SIM = Path("/sim")
ESTATICOS = Path(__file__).resolve().parent.parent / "static"
app = FastAPI(title="Conversa (simulador)")
TURNOS: dict[str, asyncio.Queue] = {}

# (segundo en que llega el texto, segundo en que el clip está listo, texto)
GUION = [
    (0.8, 2.5, "Claro, con gusto te ayudo."),
    (1.2, 4.5, "Los sábados abrimos de nueve de la mañana a dos de la tarde."),
    (1.6, 14.0, "El envío a domicilio cuesta cinco euros dentro de treinta kilómetros."),
    (2.0, 14.5, "Y es gratis si tu pedido supera los sesenta euros."),
]


async def _simular(cola: asyncio.Queue, pregunta: str) -> None:
    inicio = time.time()

    async def en(segundo: float) -> None:
        await asyncio.sleep(max(0, segundo - (time.time() - inicio)))

    await en(0.3)
    await cola.put({"tipo": "transcripcion", "texto": pregunta or "(audio simulado)"})
    await cola.put({"tipo": "contexto", "fuentes": ["Vivero Las Acacias (demo)"]})
    eventos = []
    for i, (t_texto, t_clip, texto) in enumerate(GUION, start=1):
        eventos.append((t_texto, {"tipo": "texto", "indice": i, "texto": texto}))
        eventos.append((t_clip, {"tipo": "clip", "indice": i, "url": f"/videos/clip{i}.mp4",
                                 "texto": texto, "duracion": None, "segundos": t_clip}))
    eventos.append((2.1, {"tipo": "total", "clips": len(GUION)}))
    for segundo, ev in sorted(eventos, key=lambda e: e[0]):
        await en(segundo)
        await cola.put(ev)
    await cola.put({"tipo": "fin", "cancelado": False})


@app.post("/api/turno")
async def turno(audio: UploadFile | None = File(None), texto: str = Form(""),
                sesion: str = Form("")) -> dict:
    if not texto and not audio:
        raise HTTPException(400, "Falta el audio o el texto")
    id_turno = uuid.uuid4().hex[:12]
    TURNOS[id_turno] = asyncio.Queue()
    asyncio.create_task(_simular(TURNOS[id_turno], texto.strip()))
    return {"id": id_turno}


@app.get("/api/turno/{id_turno}/eventos")
async def eventos(id_turno: str) -> StreamingResponse:
    cola = TURNOS.get(id_turno)
    if not cola:
        raise HTTPException(404)

    async def flujo():
        while True:
            ev = await cola.get()
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            if ev["tipo"] == "fin":
                return
    return StreamingResponse(flujo(), media_type="text/event-stream")


@app.get("/avatar.png")
async def avatar() -> FileResponse:
    return FileResponse(SIM / "avatar.png")


@app.api_route("/reposo.mp4", methods=["GET", "HEAD"])
async def reposo() -> FileResponse:
    return FileResponse(SIM / "reposo.mp4", media_type="video/mp4")


@app.api_route("/videos/{archivo}", methods=["GET", "HEAD"])
async def video(archivo: str) -> FileResponse:
    ruta = SIM / Path(archivo).name
    if not ruta.is_file():
        raise HTTPException(404)
    return FileResponse(ruta, media_type="video/mp4")


app.mount("/", StaticFiles(directory=ESTATICOS, html=True), name="web")
