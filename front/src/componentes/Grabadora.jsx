import { useEffect, useRef, useState } from "react";
import { avisar, avisarError } from "../avisos.js";

export default function Grabadora({ alGuardar }) {
  const [grabando, setGrabando] = useState(false);
  const [segundos, setSegundos] = useState(0);
  const [audio, setAudio] = useState(null); // { blob, url }
  const [nombre, setNombre] = useState("");
  const [transcripcion, setTranscripcion] = useState("");
  const [guardando, setGuardando] = useState(false);

  const lienzo = useRef(null);
  const grabador = useRef(null);
  const recursos = useRef({ tempo: null, animacion: null, contexto: null, flujo: null });

  const liberar = () => {
    const r = recursos.current;
    cancelAnimationFrame(r.animacion);
    clearInterval(r.tempo);
    r.flujo?.getTracks().forEach((t) => t.stop());
    if (r.contexto) { r.contexto.close(); r.contexto = null; }
  };

  useEffect(() => () => {
    if (grabador.current?.state === "recording") grabador.current.onstop = null;
    liberar();
  }, []);

  useEffect(() => () => { if (audio) URL.revokeObjectURL(audio.url); }, [audio]);

  const dibujarVumetro = (flujo) => {
    const contexto = new AudioContext();
    recursos.current.contexto = contexto;
    const analizador = contexto.createAnalyser();
    analizador.fftSize = 256;
    contexto.createMediaStreamSource(flujo).connect(analizador);

    const c = lienzo.current;
    const ctx = c.getContext("2d");
    const datos = new Uint8Array(analizador.frequencyBinCount);

    (function pintar() {
      recursos.current.animacion = requestAnimationFrame(pintar);
      analizador.getByteFrequencyData(datos);
      ctx.clearRect(0, 0, c.width, c.height);
      const ancho = c.width / datos.length;
      for (let i = 0; i < datos.length; i++) {
        const alto = (datos[i] / 255) * c.height;
        ctx.fillStyle = `hsl(${215 + (datos[i] / 255) * 45}, 90%, 62%)`;
        ctx.fillRect(i * ancho, c.height - alto, ancho - 0.5, alto);
      }
    })();
  };

  const alternar = async () => {
    if (grabador.current?.state === "recording") {
      grabador.current.stop();
      return;
    }

    let flujo;
    try {
      flujo = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch {
      avisar("Sin acceso al micrófono", "Revisa los permisos del navegador.", "error");
      return;
    }

    const trozos = [];
    const g = new MediaRecorder(flujo);
    grabador.current = g;
    recursos.current.flujo = flujo;
    g.ondataavailable = (e) => e.data.size && trozos.push(e.data);
    g.onstop = () => {
      liberar();
      const blob = new Blob(trozos, { type: g.mimeType || "audio/webm" });
      setAudio({ blob, url: URL.createObjectURL(blob) });
      setGrabando(false);
    };

    g.start();
    setGrabando(true);
    setAudio(null);

    const inicio = Date.now();
    setSegundos(0);
    recursos.current.tempo = setInterval(() => setSegundos((Date.now() - inicio) / 1000), 100);
    dibujarVumetro(flujo);
  };

  const guardar = async () => {
    if (!nombre.trim()) { avisar("Falta el nombre", "Ponle un nombre a la voz."); return; }
    if (!audio) return;
    setGuardando(true);
    try {
      await alGuardar(new File([audio.blob], "grabacion.webm"), nombre.trim(), transcripcion);
      setAudio(null);
      setNombre("");
      setTranscripcion("");
      setSegundos(0);
    } catch (err) {
      avisarError("No se pudo guardar la voz", err);
    } finally {
      setGuardando(false);
    }
  };

  return (
    <>
      <div className="grabadora">
        <button className={`btn btn-grabar ${grabando ? "grabando" : ""}`} onClick={alternar}>
          {grabando ? "■ Detener" : "● Grabar"}
        </button>
        <span className="cronometro">{segundos.toFixed(1)} s</span>
        <canvas id="vumetro" ref={lienzo} width="240" height="40"></canvas>
      </div>
      {audio && (
        <>
          <audio controls src={audio.url}></audio>
          <div>
            <input type="text" placeholder="Nombre de la voz" maxLength={60}
                   value={nombre} onChange={(e) => setNombre(e.target.value)} />
            <input type="text" maxLength={300}
                   placeholder="Transcripción del audio (opcional, mejora la clonación)"
                   value={transcripcion} onChange={(e) => setTranscripcion(e.target.value)} />
            <button className="btn btn-primario" disabled={guardando} onClick={guardar}>
              Guardar voz
            </button>
          </div>
        </>
      )}
    </>
  );
}
