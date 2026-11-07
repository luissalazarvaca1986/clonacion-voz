import { useRef, useState } from "react";
import { avisar, avisarError } from "../avisos.js";

export default function SubirVoz({ alGuardar }) {
  const [archivo, setArchivo] = useState(null);
  const [nombre, setNombre] = useState("");
  const [transcripcion, setTranscripcion] = useState("");
  const [encima, setEncima] = useState(false);
  const [procesando, setProcesando] = useState(false);
  const entrada = useRef(null);

  const elegir = (f) => {
    if (!f) return;
    setArchivo(f);
    if (!nombre) setNombre(f.name.replace(/\.[^.]+$/, ""));
  };

  const arrastre = (e) => {
    e.preventDefault();
    setEncima(e.type === "dragover");
    if (e.type === "drop") elegir(e.dataTransfer.files[0]);
  };

  const subir = async () => {
    if (!nombre.trim()) { avisar("Falta el nombre", "Ponle un nombre a la voz."); return; }
    setProcesando(true);
    try {
      await alGuardar(archivo, nombre.trim(), transcripcion);
      entrada.current.value = "";
      setArchivo(null);
      setNombre("");
      setTranscripcion("");
    } catch (err) {
      avisarError("No se pudo añadir la voz", err);
    } finally {
      setProcesando(false);
    }
  };

  return (
    <>
      <label className={`zona-archivo ${encima ? "encima" : ""}`}
             onDragOver={arrastre} onDragLeave={arrastre} onDrop={arrastre}>
        <input ref={entrada} type="file" accept="audio/*,video/*" hidden
               onChange={(e) => elegir(e.target.files[0])} />
        <span>{archivo ? archivo.name : "Arrastra un audio aquí o haz clic para elegirlo"}</span>
        <small>wav · mp3 · m4a · ogg · flac · webm</small>
      </label>
      <input type="text" placeholder="Nombre de la voz" maxLength={60}
             value={nombre} onChange={(e) => setNombre(e.target.value)} />
      <input type="text" maxLength={300}
             placeholder="Transcripción del audio (opcional, mejora la clonación)"
             value={transcripcion} onChange={(e) => setTranscripcion(e.target.value)} />
      <button className="btn btn-primario" disabled={!archivo || procesando} onClick={subir}>
        {procesando ? "Procesando…" : "Añadir a la biblioteca"}
      </button>
    </>
  );
}
