# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, Response

from app.api.dependencias import ServiciosDep
from app.esquemas.audio import Avatar, CambiosAvatar
from app.esquemas.sistema import Ok

router = APIRouter(prefix="/api/avatares", tags=["Meet-Video"])

_NO_ENCONTRADO = {404: {"description": "El avatar no existe"}}
_PNG = {200: {"content": {"image/png": {}}}}
TipoImagen = Literal["avatar", "dia", "noche", "lluvia"]
Escena = Literal["dia", "noche", "lluvia"]


@router.get("", response_model=list[Avatar], summary="Listar avatares")
def listar(s: ServiciosDep) -> list[dict]:
    return s.avatares.listar()


# Síncronas a propósito: ffmpeg normaliza las imágenes y no debe bloquear el bucle
@router.post("", response_model=Avatar, summary="Crear un avatar",
             responses={400: {"description": "Falta alguna imagen o no es válida"},
                        413: {"description": "Una imagen pasa de 25 MB"}})
def crear(s: ServiciosDep,
          nombre: str = Form(..., max_length=60, description="Nombre; también da nombre a su carpeta"),
          voz: str = Form("", description="Id de la voz con la que habla (opcional)"),
          avatar: UploadFile = File(..., description="La persona: PNG con fondo transparente"),
          dia: UploadFile = File(..., description="Fondo de día"),
          noche: UploadFile = File(..., description="Fondo de noche"),
          lluvia: UploadFile = File(..., description="Fondo con lluvia"),
          ancho: Optional[float] = Form(None, ge=0.1, le=1.0,
                                        description="Ancho sobre el fondo (fracción); vacío = 0,65"),
          izquierda: Optional[float] = Form(None, ge=0, le=0.9,
                                            description="Separación izquierda (fracción); vacío = 0,25")) -> dict:
    """Un avatar solo sirve para el Meet-Video con los tres fondos, así que los
    cuatro archivos son obligatorios. Se guarda en `avatares/<Nombre>/`."""
    return s.avatares.crear(nombre, voz, {"avatar": avatar.file, "dia": dia.file,
                                          "noche": noche.file, "lluvia": lluvia.file},
                            {"ancho": ancho, "izquierda": izquierda})


@router.patch("/{id_avatar}", response_model=Avatar, summary="Renombrar, cambiar su voz o su posición",
              responses=_NO_ENCONTRADO)
def actualizar(id_avatar: str, cambios: CambiosAvatar, s: ServiciosDep) -> dict:
    datos = cambios.model_dump(exclude_unset=True)
    return s.avatares.actualizar(id_avatar, nombre=datos.get("nombre"),
                                 voz=datos.get("voz") or None,
                                 quitar_voz="voz" in datos and not datos["voz"],
                                 ancho=datos.get("ancho"), izquierda=datos.get("izquierda"))


@router.put("/{id_avatar}/imagenes/{tipo}", response_model=Avatar,
            summary="Reemplazar la imagen o un fondo", responses=_NO_ENCONTRADO)
def reemplazar(id_avatar: str, tipo: TipoImagen, s: ServiciosDep,
               archivo: UploadFile = File(...)) -> dict:
    return s.avatares.reemplazar_imagen(id_avatar, tipo, archivo.file)


@router.delete("/{id_avatar}", response_model=Ok, summary="Eliminar un avatar",
               responses=_NO_ENCONTRADO)
def borrar(id_avatar: str, s: ServiciosDep) -> dict:
    s.avatares.borrar(id_avatar)
    return {"ok": True}


@router.get("/{id_avatar}/imagenes/{tipo}", response_class=FileResponse,
            summary="Ver la imagen o un fondo", responses={**_PNG, **_NO_ENCONTRADO})
def imagen(id_avatar: str, tipo: TipoImagen, s: ServiciosDep) -> FileResponse:
    return FileResponse(s.avatares.imagen(id_avatar, tipo), media_type="image/png")


@router.get("/{id_avatar}/zip", response_class=Response, summary="Descargar el avatar en ZIP",
            responses={200: {"content": {"application/zip": {}}}, **_NO_ENCONTRADO})
def descargar_zip(id_avatar: str, s: ServiciosDep) -> Response:
    """Imagen del avatar, los tres fondos y su avatar.json."""
    datos = s.avatares.zip(id_avatar)
    return Response(datos, media_type="application/zip", headers={
        "Content-Disposition": f'attachment; filename="avatar-{s.avatares.obtener(id_avatar)["id"]}.zip"'})


@router.get("/{id_avatar}/vista/{escena}", response_class=FileResponse,
            summary="Fotograma del Meet-Video", responses={**_PNG, **_NO_ENCONTRADO})
def vista(id_avatar: str, escena: Escena, s: ServiciosDep) -> FileResponse:
    """El avatar sobre el fondo de la escena tal y como saldrá en el video
    (1280×720, alineado abajo, con el ancho y la separación izquierda del avatar)."""
    return FileResponse(s.avatares.fotograma(id_avatar, escena), media_type="image/png",
                        headers={"Cache-Control": "no-cache"})
