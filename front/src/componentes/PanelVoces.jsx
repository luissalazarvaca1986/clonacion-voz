import { useState } from "react";
import Grabadora from "./Grabadora.jsx";
import SubirVoz from "./SubirVoz.jsx";
import { confirmar } from "../avisos.js";
import Pista from "./Pista.jsx";

// [id, icono, explicación]
const PESTANAS = [
  ["biblioteca", "📚", "Biblioteca: elige la voz que se va a clonar"],
  ["grabar", "⏺", "Grabar una voz nueva con el micrófono"],
  ["subir", "⬆", "Subir un archivo de audio (wav, mp3, m4a, ogg, flac, webm)"],
];

function Biblioteca({ voces, seleccionada, alSeleccionar, alBorrar }) {
  if (!voces) return <div className="lista-voces"></div>;
  if (!voces.length) {
    return (
      <div className="lista-voces">
        <div className="vacio">Aún no hay voces guardadas.<br />
          Graba o sube un audio para clonar una voz.</div>
      </div>
    );
  }

  return (
    <div className="lista-voces rejilla-voces">
      {voces.map((v) => (
        <div key={v.id} className={`voz ${seleccionada === v.id ? "sel" : ""}`}
             onClick={() => alSeleccionar(v.id)}>
          <Pista texto={v.transcripcion ? `«${v.transcripcion}»` : null}>
            <span className="voz-nombre">{v.nombre}</span>
          </Pista>
          <span className="voz-duracion">{v.duracion} s</span>
          <button className="icono-btn escuchar" title="Escuchar"
                  onClick={(e) => { e.stopPropagation(); new Audio(`/api/voces/${v.id}/audio`).play(); }}>
            ▶
          </button>
          <button className="icono-btn borrar" title="Eliminar"
                  onClick={(e) => {
                    e.stopPropagation();
                    confirmar(`¿Eliminar la voz «${v.nombre}»?`, "Se quitará de la biblioteca.",
                              { aceptar: "Eliminar", peligro: true })
                      .then((si) => si && alBorrar(v.id));
                  }}>
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}

export default function PanelVoces({ voces, seleccionada, alSeleccionar, alBorrar, alSubir, estilo,
                                    abierto = true, alAlternar }) {
  const [pestana, setPestana] = useState("biblioteca");

  // Al guardar una voz se vuelve a la biblioteca, con la nueva ya seleccionada.
  const subir = async (...args) => {
    await alSubir(...args);
    setPestana("biblioteca");
  };

  return (
    <section className={`tarjeta tarjeta-columna plegable ${abierto ? "" : "plegada"}`} style={estilo}>
      <div className="tarjeta-cabecera">
        <h2 className="titulo-con-info">
          <button className="plegar" onClick={() => alAlternar?.()} aria-expanded={abierto}
                  title={abierto ? "Plegar" : "Desplegar"}>{abierto ? "▾" : "▸"}</button>
          <span className="titulo-plegable" onClick={() => alAlternar?.()}>Voces disponibles</span>
          <Pista texto="Sube o graba entre 5 y 15 segundos de voz limpia. Sin referencia, el modelo usa su voz por defecto.">
            <span className="info" aria-label="Información">ⓘ</span>
          </Pista>
        </h2>
        <div className="barra-iconos" role="tablist" aria-label="Voces disponibles">
          {PESTANAS.map(([id, icono, ayuda]) => (
            <Pista key={id} texto={ayuda} lado="bottom">
              <button role="tab" aria-selected={pestana === id} aria-label={ayuda}
                      className={`icono-pestana ${pestana === id ? "activa" : ""} ${id === "grabar" ? "es-grabar" : ""}`}
                      onClick={() => { setPestana(id); alAlternar?.(true); }}>
                {icono}
              </button>
            </Pista>
          ))}
        </div>
      </div>

      {/* Los paneles se ocultan en vez de desmontarse para no perder una grabación a medias */}
      <div className={`tarjeta-desplazable ${abierto ? "" : "oculto"}`}>
      <div className={`panel ${pestana === "biblioteca" ? "" : "oculto"}`}>
        <Biblioteca voces={voces} seleccionada={seleccionada}
                    alSeleccionar={alSeleccionar} alBorrar={alBorrar} />
      </div>
      <div className={`panel ${pestana === "grabar" ? "" : "oculto"}`}>
        <Grabadora alGuardar={subir} />
      </div>
      <div className={`panel ${pestana === "subir" ? "" : "oculto"}`}>
        <SubirVoz alGuardar={subir} />
      </div>
      </div>
    </section>
  );
}
