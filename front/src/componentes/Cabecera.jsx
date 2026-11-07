import Pista from "./Pista.jsx";

// Solo el estado (punto) y el nombre; el dato concreto aparece al pasar el ratón
function Pastilla({ punto, etiqueta, valor, titulo = "" }) {
  const estado = { ok: "Disponible", mal: "No disponible", medio: "Disponible con limitaciones" }[punto];
  return (
    <Pista lado="bottom" texto={
      <div className="pista-pastilla">
        <div className="pista-pastilla-estado"><span className={`punto ${punto}`}></span>{estado}</div>
        <b>{valor}</b>
        {titulo && <div className="pista-pastilla-detalle">{titulo}</div>}
      </div>
    }>
      <span className="pastilla" aria-label={`${etiqueta}: ${estado}. ${valor}`}>
        <span className={`punto ${punto}`}></span>{etiqueta}
      </span>
    </Pista>
  );
}

export default function Cabecera({ sistema: s }) {
  let pastillas = null;
  if (s) {
    const modelo = s.modelo ? s.modelo.split(/[\\/]/).pop() : "no encontrado";
    const gpus = s.dispositivos.filter((d) => !d.id.toLowerCase().startsWith("cpu"));
    pastillas = (
      <>
        <Pastilla punto={s.binario_ok && s.soporta_qwen3tts ? "ok" : "mal"} etiqueta="llama.cpp"
                  valor={s.version || "no encontrado"} titulo={s.binario} />
        <Pastilla punto={s.modelo_ok && s.mmproj_ok ? "ok" : "mal"} etiqueta="Modelo"
                  valor={s.modelo_ok ? modelo : "Falta el modelo"}
                  titulo={s.mmproj_ok ? `Vocoder: ${s.mmproj.split(/[\\/]/).pop()}` : "Falta el vocoder (mmproj)"} />
        <Pastilla punto={gpus.length ? "ok" : "medio"} etiqueta="Cómputo"
                  valor={gpus.length ? `${gpus.length} GPU · CPU` : "solo CPU"}
                  titulo={gpus.map((g) => `${g.id}: ${g.nombre}`).join("\n")} />
        <Pastilla punto={s.ffmpeg ? "ok" : "medio"} etiqueta="ffmpeg"
                  valor={s.ffmpeg ? "Instalado" : "No instalado"}
                  titulo={s.ffmpeg ? "Convierte audios y compone los videos" : "Sin él no se pueden grabar voces ni generar videos"} />
        {s.llm?.configurado && (
          <Pastilla punto={s.llm.disponible ? "ok" : "medio"} etiqueta="LLM"
                    valor={s.llm.disponible ? s.llm.modelo : "sin conexión"}
                    titulo={`Segmentación por emociones · se carga bajo demanda en CPU${s.llm.activo ? " (cargado ahora)" : ""}`} />
        )}
      </>
    );
  }

  return (
    <header className="cabecera">
      <div className="marca">
        <img className="logo" src="/icono.svg" alt="" width="40" height="40" />
        <div>
          <h1>Sintetizador de audio/video</h1>
        </div>
      </div>
      <div className="pastillas">{pastillas}</div>
    </header>
  );
}
