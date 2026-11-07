import { useEffect, useRef, useState } from "react";
import { recortar } from "../util.js";
import { ESTILO_EMOCION } from "./Segmentos.jsx";
import { ESCENAS } from "../constantes.js";

const version = (salida) => `?v=${encodeURIComponent(salida.fecha ?? "")}`;
const urlParte = (salida, archivo) =>
  `/api/salidas/${salida.archivo.replace(/\.wav$/, "")}/partes/${archivo}${version(salida)}`;

// Solo se cierra con sus propios botones (✕ de la cabecera o Cancelar del pie):
// un clic fuera o la tecla Escape no lo cierran.
export function Modal({ titulo, alCerrar, pie, children }) {
  useEffect(() => {
    document.body.classList.add("con-modal");
    return () => document.body.classList.remove("con-modal");
  }, []);
  return (
    <div className="modal-fondo">
      <div className="modal" role="dialog" aria-modal="true" aria-label={titulo}>
        <div className="modal-cabecera">
          <h3>{titulo}</h3>
          <button className="icono-btn" onClick={alCerrar} title="Cerrar">✕</button>
        </div>
        <div className="modal-cuerpo">{children}</div>
        <div className="modal-pie">
          {pie}
          <button className="btn btn-sec" onClick={alCerrar} autoFocus>Cancelar</button>
        </div>
      </div>
    </div>
  );
}

function VistaPrevia({ elemento, reproducir }) {
  const ref = useRef(null);
  // Reproducir solo cuando está cargado: si se llama a play() antes, el
  // navegador abre la salida de audio sobre la marcha y se come el inicio.
  useEffect(() => {
    const medio = ref.current;
    if (!reproducir || !medio) return undefined;
    const alCargar = () => { medio.currentTime = 0; medio.play().catch(() => {}); };
    medio.addEventListener("canplaythrough", alCargar, { once: true });
    return () => medio.removeEventListener("canplaythrough", alCargar);
  }, [elemento, reproducir]);

  return (
    <div className="vista-previa">
      {elemento.video
        ? <video ref={ref} key={elemento.video} controls src={elemento.video}></video>
        : (
          <div className="vista-audio">
            <span className="vista-audio-icono">🎙</span>
            <audio ref={ref} key={elemento.audio} controls src={elemento.audio}></audio>
          </div>
        )}
      <div className="vista-previa-pie">
        <span className="icono-tipo">{elemento.video ? "🎬" : "🎙"}</span>
        <span>{elemento.etiqueta}</span>
      </div>
    </div>
  );
}

function Fila({ icono, titulo, detalle, meta, activo, alReproducir, descargas, alEditar }) {
  return (
    <div className={`fila-elemento ${activo ? "activo" : ""}`}>
      <button className="boton-play" onClick={alReproducir} title="Reproducir en la vista previa"
              aria-label={`Reproducir ${titulo}`}>▶</button>
      <span className="icono-tipo" title={icono === "🎬" ? "Video y audio" : "Solo audio"}>{icono}</span>
      <span className="fila-elemento-texto">
        <b>{titulo}</b>
        {detalle && <span>{detalle}</span>}
      </span>
      <span className="fila-elemento-meta">{meta}</span>
      {alEditar && (
        <button className="btn btn-sec btn-pequeno" onClick={alEditar} title="Cambiar sus ajustes y regenerarla">
          ✎ Editar
        </button>
      )}
      <span className="fila-elemento-descargas">
        {descargas.map(([etiqueta, url, nombre]) => (
          <a key={url} className="btn btn-sec btn-pequeno" href={url} download={nombre}
             title={`Descargar ${etiqueta}`}>⭳ {etiqueta}</a>
        ))}
      </span>
    </div>
  );
}

export default function DetalleGeneracion({ salida, autoplay = false, alEditar }) {
  const partes = salida.segmentos ?? [];
  const total = {
    clave: "total",
    audio: `/api/salidas/${salida.archivo}${version(salida)}`,
    video: salida.video ? `/api/videos/${salida.video}${version(salida)}` : null,
    etiqueta: "Resultado completo",
  };
  const elementoParte = (p) => ({
    clave: `parte-${p.indice}`,
    audio: urlParte(salida, p.audio),
    video: p.video ? urlParte(salida, p.video) : null,
    etiqueta: `Parte ${p.indice} · ${recortar(p.texto, 70)}`,
  });

  const [actual, setActual] = useState(total);
  // Si la generación cambia (p. ej. tras regenerar un segmento) se vuelve al total
  useEffect(() => { setActual(total); }, [salida.fecha]); // eslint-disable-line react-hooks/exhaustive-deps
  const [reproducir, setReproducir] = useState(autoplay);
  const elegir = (elemento) => { setActual(elemento); setReproducir(true); };

  return (
    <div className="detalle-generacion">
      <VistaPrevia elemento={actual} reproducir={reproducir} />

      <h4 className="detalle-seccion">Resultado</h4>
      <Fila icono={total.video ? "🎬" : "🎙"} titulo="Resultado completo"
            detalle={partes.length > 1 ? `${partes.length} partes unidas` : null}
            meta={`${salida.duracion} s`} activo={actual.clave === "total"}
            alReproducir={() => elegir(total)}
            descargas={[...(total.video ? [["MP4", total.video, salida.video]] : []),
                        ["WAV", total.audio, salida.archivo]]} />

      {partes.length > 0 && (
        <>
          <h4 className="detalle-seccion">Partes</h4>
          <div className="lista-partes">
            {partes.map((p) => {
              const elemento = elementoParte(p);
              const estilo = ESTILO_EMOCION[p.emocion];
              const escena = ESCENAS.find((e) => e.id === p.escena);
              return (
                <Fila key={p.indice} icono={p.video ? "🎬" : "🎙"} titulo={`Parte ${p.indice}`}
                      detalle={`«${recortar(p.texto, 90)}»`}
                      meta={[estilo && p.emocion !== "neutro" ? estilo.emoji : null,
                             p.avatar ? `🧑 ${p.avatar}` : null, escena?.emoji, `${p.duracion} s`]
                        .filter(Boolean).join(" · ")}
                      activo={actual.clave === elemento.clave} alReproducir={() => elegir(elemento)}
                      alEditar={alEditar && salida.editable ? () => alEditar(p.indice) : null}
                      descargas={[...(p.video ? [["MP4", elemento.video, null]] : []),
                                  ["WAV", elemento.audio, null]]} />
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}
