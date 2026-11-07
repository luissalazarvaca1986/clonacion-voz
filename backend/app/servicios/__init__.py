# -*- coding: utf-8 -*-
"""Capa de servicios: toda la lógica, sin dependencias de HTTP.

`crear_servicios` monta el grafo de objetos una sola vez; las rutas lo reciben
por inyección de dependencias (ver `app/api/dependencias.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.nucleo.ajustes import (MAX_BYTES_IMAGEN, MAX_BYTES_REFERENCIA, MAX_HISTORIAL,
                                MAX_SEGUNDOS_ANIMACION, VIDA_TAREAS_SEGUNDOS, Rutas)
from app.nucleo.configuracion import RepositorioConfig


@dataclass
class Servicios:
    rutas: Rutas
    config: RepositorioConfig
    entorno: "DetectorEntorno"
    voces: "BibliotecaVoces"
    salidas: "AlmacenSalidas"
    tareas: "GestorTareas"
    generacion: "ServicioGeneracion"
    descarga: "GestorDescarga"
    llm: "ClienteLLM"
    avatares: "GaleriaAvatares"
    animacion: "ServicioAnimacion"
    proyectos: "RepositorioProyectos"


def crear_servicios(rutas: Rutas) -> Servicios:
    from app.servicios.analisis import AnalizadorEmocional
    from app.servicios.animacion import ProveedorSadTalker, ServicioAnimacion
    from app.servicios.avatares import GaleriaAvatares
    from app.servicios.descarga import GestorDescarga
    from app.servicios.entorno import DetectorEntorno
    from app.servicios.generacion import ServicioGeneracion
    from app.servicios.llm import ClienteLLM
    from app.servicios.salidas import AlmacenSalidas
    from app.servicios.sintesis import GestorTareas
    from app.servicios.voces import BibliotecaVoces

    from app.repositorios.proyectos import RepositorioMemoria, RepositorioMongo

    config = RepositorioConfig(rutas.config)
    proyectos = RepositorioMongo(rutas.mongo_url) if rutas.mongo_url else RepositorioMemoria()
    entorno = DetectorEntorno(rutas.modelo, rutas.base)
    voces = BibliotecaVoces(rutas.voces, MAX_BYTES_REFERENCIA)
    salidas = AlmacenSalidas(rutas.salidas, MAX_HISTORIAL, rutas.videos, proyectos)
    tareas = GestorTareas(rutas.tmp, VIDA_TAREAS_SEGUNDOS)
    # llama-server viene en la misma carpeta que llama-tts
    binario_tts = entorno.buscar_binario()
    llm = ClienteLLM(rutas.llm_url, rutas.llm_timeout, modelo=rutas.llm_modelo,
                     binario=str(Path(binario_tts).with_name("llama-server")) if binario_tts else "",
                     hilos=rutas.llm_hilos)
    avatares = GaleriaAvatares(rutas.avatares, MAX_BYTES_IMAGEN, voces)
    # Motores de animación: para añadir otro, se registra aquí
    animacion = ServicioAnimacion(
        [ProveedorSadTalker(rutas.animador_url, rutas.animacion, rutas.animador_timeout)],
        rutas.animacion, avatares, salidas, tareas, rutas.videos,
        MAX_BYTES_IMAGEN, MAX_BYTES_REFERENCIA, MAX_SEGUNDOS_ANIMACION)
    return Servicios(
        rutas=rutas,
        config=config,
        entorno=entorno,
        voces=voces,
        salidas=salidas,
        tareas=tareas,
        generacion=ServicioGeneracion(config, entorno, voces, salidas, tareas,
                                      AnalizadorEmocional(llm), avatares, animacion),
        descarga=GestorDescarga(rutas.modelo, al_terminar=entorno.limpiar_cache),
        llm=llm,
        avatares=avatares,
        animacion=animacion,
        proyectos=proyectos,
    )
