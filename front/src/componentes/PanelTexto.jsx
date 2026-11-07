import { useEffect, useRef, useState } from "react";
import { api, postJson, recortar } from "../util.js";
import Segmentos, { AJUSTES_ANIMACION, Deslizador, etiquetaEmocion } from "./Segmentos.jsx";
import { ESCENAS } from "../constantes.js";
import DetalleGeneracion from "./DetalleGeneracion.jsx";
import Pista from "./Pista.jsx";

const CAMPOS_AJUSTES = ["temp", "top_p", "top_k", "semilla",
                        "chars_por_bloque", "pausa_ms", "max_frames", "hilos"];

const AJUSTES_INICIALES = {
  idioma: "es", dispositivo: "auto",
  temp: "0.8", top_p: "0.95", top_k: "40", semilla: "-1",
  chars_por_bloque: "280", pausa_ms: "150", max_frames: "1200", hilos: "0",
};

const EJEMPLO =
  "Hola, esta es una prueba de clonación de voz ejecutándose por completo en " +
  "mi propio ordenador. El modelo Qwen3 TTS funciona tanto en procesador como " +
  "en tarjeta gráfica, sin enviar nada a internet.";

// [campo, etiqueta, ayuda, atributos del input]
const CAMPOS_NUMERICOS = [
  ["top_k", "Top-K", "0 = desactivado", { min: 0, max: 200 }],
  ["semilla", "Semilla", "-1 = aleatoria en cada intento", { min: -1 }],
  ["chars_por_bloque", "Caracteres por bloque", "El texto largo se divide y se une al final",
   { min: 80, max: 600, step: 10 }],
  ["pausa_ms", "Pausa entre bloques (ms)", "Silencio insertado en las uniones",
   { min: 0, max: 2000, step: 25 }],
  ["max_frames", "Máx. frames por bloque", "12 frames = 1 segundo de audio",
   { min: 120, max: 6000, step: 60 }],
  ["hilos", "Hilos de CPU", "0 = automático", { min: 0, max: 128 }],
];

function leerPreferencia(clave, defecto) {
  try { return JSON.parse(localStorage.getItem(clave)) ?? defecto; } catch { return defecto; }
}

function guardarPreferencia(clave, valor) {
  try { localStorage.setItem(clave, JSON.stringify(valor)); } catch { /* sin almacenamiento */ }
}

export default function PanelTexto({ sistema, catalogoEmociones, vozSeleccionada, alGenerar,
                                    voces, avatares, motores, estilo }) {
  const [texto, setTexto] = useState("");
  const [ajustes, setAjustes] = useState(AJUSTES_INICIALES);
  const [guardado, setGuardado] = useState(false);

  // Cómo se divide el texto: único (todo junto), ia (análisis de emociones) o manual
  const [modo, setModo] = useState(() => leerPreferencia("modo-segmentos",
    leerPreferencia("voz-emocional", false) ? "ia" : "unico"));
  const [segmentacionIA, setSegmentacionIA] = useState(() => leerPreferencia("segmentacion-ia", modo === "ia"));
  const emocional = modo !== "unico";   // en ia y manual cada segmento lleva su emoción
  const manual = modo === "manual";
  const [conVideo, setConVideo] = useState(() => leerPreferencia("meet-video", false));
  const [escenaGeneral, setEscenaGeneral] = useState(() => leerPreferencia("meet-escena", "dia"));

  // Segmentos: los del análisis de emociones o, sin él, el texto completo como uno solo
  const [plan, setPlan] = useState(null);   // { texto, origen, segmentos }
  const [abierto, setAbierto] = useState(null);
  const [analizando, setAnalizando] = useState(false);
  const [errorAnalisis, setErrorAnalisis] = useState("");

  const [generando, setGenerando] = useState(false);
  const [progreso, setProgreso] = useState(null); // { pct, texto }
  const [estados, setEstados] = useState({});     // { [segmento]: { audio, video } }
  const [consola, setConsola] = useState(null);
  const [resultado, setResultado] = useState(null);

  const tarea = useRef(null);
  const fuente = useRef(null);
  const cajaConsola = useRef(null);
  const preConsola = useRef(null);
  const peticionAnalisis = useRef(0);

  useEffect(() => {
    if (!sistema) return;
    setAjustes((a) => {
      const nuevos = { ...a, idioma: sistema.config.idioma, dispositivo: sistema.config.dispositivo };
      for (const campo of CAMPOS_AJUSTES) {
        if (sistema.config[campo] !== undefined) nuevos[campo] = String(sistema.config[campo]);
      }
      return nuevos;
    });
  }, [sistema]);

  useEffect(() => () => fuente.current?.close(), []);
  useEffect(() => {
    if (preConsola.current) preConsola.current.scrollTop = preConsola.current.scrollHeight;
  }, [consola]);

  const llm = sistema?.llm;
  const llmDisponible = Boolean(llm?.disponible);
  const avataresUtiles = (avatares ?? []).filter((a) => a.completo);
  const sadtalker = (motores ?? []).find((m) => m.id === "sadtalker");
  const puedeAnimar = Boolean(sadtalker?.disponible);
  const animar = conVideo;   // con Meet-Video el avatar siempre se anima

  const preferencia = (clave, fijar) => (valor) => { fijar(valor); guardarPreferencia(clave, valor); };
  const cambiar = (campo) => (e) => setAjustes((a) => ({ ...a, [campo]: e.target.value }));

  const ajustesActuales = () => {
    const cfg = { idioma: ajustes.idioma, dispositivo: ajustes.dispositivo };
    for (const campo of CAMPOS_AJUSTES) cfg[campo] = parseFloat(ajustes[campo]);
    return cfg;
  };
  const baseSugerencia = () => {
    const b = ajustesActuales();
    return { temp: b.temp, top_p: b.top_p, top_k: b.top_k, pausa_ms: b.pausa_ms };
  };

  const segmentos = manual ? (plan?.origen === "manual" ? plan.segmentos : null)
    : (plan && plan.origen !== "manual" && plan.texto && plan.texto === texto.trim() ? plan.segmentos : null);
  // En manual el texto es la suma de los segmentos
  const limpio = manual ? (segmentos ?? []).map((sg) => sg.texto.trim()).filter(Boolean).join(" ")
    : texto.trim();
  const segundos = Math.round(limpio.length / 14); // ~14 caracteres por segundo de habla natural

  // ---- Segmentación ----------------------------------------------------------
  const conSugeridos = (lista) => lista.map((sg) => ({ ...sg, sugeridos: sg.ajustes }));

  const analizar = async (textoAnalizar = limpio) => {
    if (!textoAnalizar) return;
    const n = ++peticionAnalisis.current;
    setAnalizando(true);
    setErrorAnalisis("");
    try {
      const r = await postJson("/api/analizar", { texto: textoAnalizar, ...ajustesActuales() });
      if (n === peticionAnalisis.current) {
        setPlan({ texto: textoAnalizar, origen: r.origen, segmentos: conSugeridos(r.segmentos) });
        setAbierto(null);
      }
    } catch (err) {
      if (n === peticionAnalisis.current) setErrorAnalisis(err.message);
    } finally {
      if (n === peticionAnalisis.current) setAnalizando(false);
    }
  };

  // Con IA: el análisis se lanza solo al dejar de escribir
  useEffect(() => {
    if (modo !== "ia" || !limpio || generando || plan?.texto === limpio) return undefined;
    if (sistema && !llmDisponible && !/\[[a-záéíóú]+/i.test(limpio)) return undefined;
    const espera = setTimeout(() => analizar(limpio), 1200);
    return () => clearTimeout(espera);
  }, [modo, limpio, llmDisponible]); // eslint-disable-line react-hooks/exhaustive-deps

  // Texto completo: un único segmento (conserva su configuración)
  useEffect(() => {
    if (modo !== "unico" || !limpio || generando) return undefined;
    let vigente = true;
    (async () => {
      const previo = plan?.origen === "unico" ? plan.segmentos[0] : null;
      const ajustesSeg = previo?.ajustes ?? await postJson("/api/emociones/sugerencia",
        { emocion: "neutro", intensidad: "media", ...baseSugerencia() }).catch(() => null);
      if (!vigente) return;
      setPlan({ texto: limpio, origen: "unico", segmentos: [{
        texto: limpio, emocion: previo?.emocion ?? "neutro", intensidad: previo?.intensidad ?? "media",
        voz: previo?.voz ?? null, avatar: previo?.avatar ?? null, escena: previo?.escena ?? null,
        ajustes: ajustesSeg, sugeridos: previo?.sugeridos ?? ajustesSeg,
      }] });
    })();
    return () => { vigente = false; };
  }, [modo, limpio]); // eslint-disable-line react-hooks/exhaustive-deps

  const segmentoNuevo = async (texto_ = "", previo = null) => {
    const ajustesSeg = await postJson("/api/emociones/sugerencia",
      { emocion: "neutro", intensidad: "media", ...baseSugerencia() }).catch(() => null);
    return { texto: texto_, emocion: "neutro", intensidad: "media", voz: previo?.voz ?? null,
             avatar: null, escena: previo?.escena ?? null,
             ajustes: ajustesSeg, sugeridos: ajustesSeg };
  };

  // Manual: parte de los segmentos que ya hubiera (o del texto escrito) y se editan a mano
  const cambiarModo = async (nuevo) => {
    preferencia("modo-segmentos", setModo)(nuevo);
    setAbierto(null);
    if (nuevo !== "manual" || plan?.origen === "manual") return;
    const actuales = plan && plan.texto === texto.trim() ? plan.segmentos : null;
    setPlan({ texto: "", origen: "manual",
              segmentos: actuales?.length ? actuales : [await segmentoNuevo(texto.trim())] });
  };

  // Si se entra ya en manual (preferencia guardada), arranca con un segmento vacío
  useEffect(() => {
    if (modo !== "manual" || plan?.origen === "manual") return undefined;
    let vigente = true;
    segmentoNuevo(texto.trim()).then((sg) => {
      if (vigente) setPlan((p) => (p?.origen === "manual" ? p : { texto: "", origen: "manual", segmentos: [sg] }));
    });
    return () => { vigente = false; };
  }, [modo]); // eslint-disable-line react-hooks/exhaustive-deps

  const anadirSegmento = async () => {
    const ultimo = segmentos?.[segmentos.length - 1];
    const nuevo = await segmentoNuevo("", ultimo);
    setPlan((p) => ({ ...p, segmentos: [...p.segmentos, nuevo] }));
    setAbierto(segmentos.length);
  };

  const cambiarSegmentos = (lista) => setPlan((p) => ({
    ...p, origen: ["unico", "manual"].includes(p.origen) ? p.origen : "usuario", segmentos: lista }));

  // Cambiar emoción o intensidad trae los ajustes que sugiere el analizador
  const sugerir = async (i, emocion, intensidad) => {
    const sugeridos = await postJson("/api/emociones/sugerencia",
      { emocion, intensidad, ...baseSugerencia() });
    setPlan((p) => ({
      ...p, origen: ["unico", "manual"].includes(p.origen) ? p.origen : "usuario",
      segmentos: p.segmentos.map((sg, j) => (j === i
        ? { ...sg, emocion, intensidad, ajustes: sugeridos, sugeridos } : sg)),
    }));
  };

  // ---- Generación ------------------------------------------------------------
  const faltanAvatares = conVideo && segmentos ? segmentos.filter((sg) => !sg.avatar).length : 0;
  const vacios = manual && segmentos ? segmentos.filter((sg) => !sg.texto.trim()).length : 0;
  let motivoBloqueo = "";
  if (!limpio) motivoBloqueo = manual ? "Escribe el texto de los segmentos." : "Escribe el texto.";
  else if (vacios) motivoBloqueo = `Hay ${vacios} segmento${vacios === 1 ? "" : "s"} sin texto.`;
  else if (!segmentos) motivoBloqueo = analizando ? "Analizando las emociones…" : "Preparando los segmentos…";
  else if (conVideo && !avataresUtiles.length) motivoBloqueo = "Crea un avatar primero.";
  else if (conVideo && sistema && !puedeAnimar) motivoBloqueo = "El motor de animación no está disponible.";
  else if (faltanAvatares) {
    motivoBloqueo = modo === "unico" ? "Elige el avatar en «Ajustes generales del video»."
      : `Elige el avatar de ${faltanAvatares} segmento${faltanAvatares === 1 ? "" : "s"}.`;
  }

  // Un segmento largo se sintetiza en varios bloques: el bloque cae en el
  // segmento cuyo texto lo contiene
  const segmentoDeBloque = (d) => {
    const n = (segmentos ?? []).findIndex((sg) => sg.texto.includes(d.texto.slice(0, 40)));
    return n >= 0 ? n + 1 : d.indice;
  };

  const cerrarFlujo = () => {
    fuente.current?.close();
    fuente.current = null;
    tarea.current = null;
    setGenerando(false);
  };

  const comenzar = async () => {
    if (generando || motivoBloqueo) return;
    setGenerando(true);
    setResultado(null);
    setEstados({});
    setAbierto(null);
    setConsola("");
    setProgreso({ pct: 0, texto: "Preparando…" });

    let t;
    try {
      const peticion = {
        texto: limpio, voz: vozSeleccionada, ...ajustesActuales(),
        modo: modo === "ia" ? "emocional" : "simple",
        segmentos: segmentos.map(({ texto: tx, emocion, intensidad, voz, avatar, escena, ajustes: a }) =>
          ({ texto: tx, emocion, intensidad, voz, avatar, escena,
             ajustes: modo === "unico" && a
               ? { escala_expresion: a.escala_expresion, estilo_pose: a.estilo_pose } : a })),
      };
      if (conVideo) {
        Object.assign(peticion, { video: true, escena: escenaGeneral, animado: true });
      }
      t = await postJson("/api/generar", peticion);
    } catch (err) {
      setGenerando(false);
      setProgreso((p) => ({ ...p, texto: "✕ " + err.message }));
      return;
    }

    tarea.current = t.id;
    const titulo = recortar(limpio, 60);
    const f = new EventSource(`/api/tarea/${t.id}/eventos`);
    fuente.current = f;

    f.onmessage = (ev) => {
      const d = JSON.parse(ev.data);
      if (d.tipo === "log") {
        setConsola((c) => c + d.linea + "\n");
      } else if (d.tipo === "bloque") {
        const n = segmentoDeBloque(d);
        setEstados((e) => ({ ...e, [n]: { ...e[n], audio: "en_curso" } }));
        const emocion = d.emocion ? `${etiquetaEmocion(catalogoEmociones, d.emocion)} · ` : "";
        setProgreso({ pct: ((d.indice - 1) / d.total) * 100,
                      texto: `🎙 Audio ${d.indice} de ${d.total} · ${emocion}${recortar(d.texto, 60)}` });
      } else if (d.tipo === "segmento") {
        setEstados((e) => ({ ...e, [d.indice]: { ...e[d.indice], [d.etapa]: d.estado } }));
        if (d.etapa === "video" && d.estado === "en_curso") {
          setProgreso({ pct: ((d.indice - 1) / d.total) * 100,
                        texto: `🎬 Video del segmento ${d.indice} de ${d.total}` });
        }
      } else if (d.tipo === "video") {
        setProgreso({ pct: 0, texto: "🎬 Componiendo el video de cada segmento" });
      } else if (d.tipo === "progreso") {
        // Fotogramas reales que informa el motor de animación
        setProgreso((p) => ({ pct: (d.hecho / d.total) * 100,
                              texto: `${p.texto.split(" · ")[0]} · ${d.fase}: fotograma ${d.hecho} de ${d.total}` }));
      } else if (d.tipo === "fin") {
        setProgreso({ pct: 100, texto: `✓ Listo en ${d.segundos} s · ${d.duracion} s` });
        setResultado({ archivo: d.archivo, video: d.video ?? null, duracion: d.duracion,
                       segmentos: d.segmentos ?? [], titulo });
        cerrarFlujo();
        alGenerar();
      } else if (d.tipo === "error") {
        setProgreso((p) => ({ ...p, texto: "✕ Error: " + d.mensaje }));
        if (cajaConsola.current) cajaConsola.current.open = true;
        cerrarFlujo();
      } else if (d.tipo === "cancelada") {
        setProgreso((p) => ({ ...p, texto: "Generación cancelada." }));
        cerrarFlujo();
      }
    };
    f.onerror = () => {
      if (fuente.current) {
        setProgreso((p) => ({ ...p, texto: "Se perdió la conexión con el servidor." }));
        cerrarFlujo();
      }
    };
  };

  const cancelar = async () => {
    if (tarea.current) await api(`/api/tarea/${tarea.current}/cancelar`, { method: "POST" });
  };

  const guardarAjustes = async () => {
    await postJson("/api/config", ajustesActuales());
    setGuardado(true);
    setTimeout(() => setGuardado(false), 1800);
  };

  return (
    <section className="tarjeta tarjeta-proyecto" style={estilo}>
      <div className="tarjeta-cabecera">
        <h2>Texto a sintetizar</h2>
        <div className="selector-escena barra-modo" role="radiogroup" aria-label="Forma de escribir">
          {[["texto", "📝 Un solo texto", "Escribes todo el texto en un único cuadro. Puedes dejar que la IA lo divida en segmentos por emociones."],
            ["manual", "✍ Manual", "Creas tú cada segmento (➕ Añadir segmento) con su texto, emoción, voz, avatar y fondo."]]
            .map(([id, etq, ayuda]) => {
              const activa = id === "manual" ? manual : !manual;
              return (
                <Pista key={id} texto={ayuda} lado="bottom">
                  <button role="radio" aria-checked={activa} disabled={generando} className={activa ? "activa" : ""}
                          onClick={() => cambiarModo(id === "manual" ? "manual" : (segmentacionIA ? "ia" : "unico"))}>
                    {etq}
                  </button>
                </Pista>
              );
            })}
        </div>
      </div>
      {manual ? (
        <p className="ayuda modo-manual-ayuda">✍ Modo manual: escribe cada segmento por separado y
          elige su emoción, voz, avatar y fondo. {limpio.length} caracteres · ≈{segundos} s de audio.</p>
      ) : (
        <>
          <div className="fila-ia">
            <Pista texto={!sistema ? "Comprobando la IA…" : llmDisponible
              ? `Al dejar de escribir, la IA (${llm.modelo}) divide el texto en segmentos según su emoción y sugiere la configuración de cada uno. También puedes marcar a mano: [alegria:alta] ¡Qué bien! [tristeza] Pero…`
              : "La IA no está disponible. Aun así puedes marcar los segmentos a mano en el texto: [alegria:alta] ¡Qué bien! [tristeza] Pero…"}>
              <label className="interruptor interruptor-pequeno">
                <input type="checkbox" checked={modo === "ia"} disabled={generando}
                       onChange={(e) => {
                         guardarPreferencia("segmentacion-ia", e.target.checked);
                         setSegmentacionIA(e.target.checked);
                         cambiarModo(e.target.checked ? "ia" : "unico");
                       }} />
                <span className="interruptor-pista"></span>
                <span>🎭 Segmentación por IA{sistema && !llmDisponible ? " ⚠" : ""}</span>
              </label>
            </Pista>
            {modo === "ia" && limpio && (
              <Pista texto="Vuelve a analizar el texto: se pierden los cambios hechos a los segmentos">
                <button className="btn-enlace" disabled={analizando || generando} onClick={() => analizar()}>
                  {analizando ? "Analizando…" : "↻ Volver a analizar"}
                </button>
              </Pista>
            )}
          </div>
          <textarea rows={6} maxLength={20000} value={texto} disabled={generando}
            placeholder="Escribe aquí lo que quieres que diga la voz…"
            onChange={(e) => setTexto(e.target.value)}
            onKeyDown={(e) => { if (e.ctrlKey && e.key === "Enter") comenzar(); }} />
          <div className="fila-info">
            <span>{limpio.length} caracteres · ≈{segundos} s de audio</span>
            <button className="btn-enlace" onClick={() => setTexto(EJEMPLO)} disabled={generando}>Insertar ejemplo</button>
          </div>
        </>
      )}

      {/* ---- Meet-Video: interruptor y fondo por defecto en la misma fila ---- */}
      <div className={`marca-opcion meet-bloque ${conVideo ? "activa" : ""}`}>
        <div className="meet-fila">
          <Pista texto="Genera además un video tipo videollamada con el avatar animado (labios, parpadeo y expresión) que dice cada segmento. El avatar, el fondo, la expresión y el estilo de cabeza se eligen en cada segmento. Tarda unas 15-20 veces la duración del audio." lado="right">
            <label className="interruptor">
              <input type="checkbox" checked={conVideo} disabled={generando}
                     onChange={(e) => preferencia("meet-video", setConVideo)(e.target.checked)} />
              <span className="interruptor-pista"></span>
              <span>🎬 Meet-Video</span>
            </label>
          </Pista>
          {conVideo && (
            <Pista texto="Fondo de los segmentos que no elijan otro" lado="left">
              <div className="selector-escena" role="radiogroup" aria-label="Fondo por defecto">
                {ESCENAS.map((e) => (
                  <button key={e.id} role="radio" aria-checked={escenaGeneral === e.id}
                          className={escenaGeneral === e.id ? "activa" : ""} disabled={generando}
                          onClick={() => preferencia("meet-escena", setEscenaGeneral)(e.id)}>
                    {e.emoji} {e.etiqueta}
                  </button>
                ))}
              </div>
            </Pista>
          )}
        </div>
        {conVideo && !avataresUtiles.length && (
          <small className="aviso-linea">⚠ Aún no hay avatares completos: crea uno en «Avatares».</small>
        )}
        {conVideo && sistema && !puedeAnimar && (
          <small className="aviso-linea">⚠ El motor de animación no está disponible: revisa el contenedor clonar-voz-animador.</small>
        )}
      </div>

      <div className="campos">
        <label>
          <span>Idioma</span>
          <select value={ajustes.idioma} onChange={cambiar("idioma")}>
            {sistema && Object.entries(sistema.idiomas).map(([cod, nom]) =>
              <option key={cod} value={cod}>{nom}</option>)}
          </select>
        </label>
        <label>
          <span>Procesamiento del audio</span>
          <select value={ajustes.dispositivo} onChange={cambiar("dispositivo")}>
            <option value="auto">Automático (mejor disponible)</option>
            {sistema?.dispositivos.map((d) => (
              <option key={d.id} value={d.id}>
                GPU · {d.nombre}{d.id === sistema.dispositivo_preferido ? " ★" : ""}
              </option>
            ))}
            <option value="cpu">Solo CPU ({sistema?.cpus ?? "?"} núcleos)</option>
          </select>
        </label>
      </div>

      {/* ---- Segmentos ---- */}
      {errorAnalisis && <p className="error-analisis">✕ {errorAnalisis}</p>}
      {!manual && limpio && !segmentos && (
        <div className="segmentos-espera">
          {analizando ? "🎭 Analizando las emociones del texto…" : "Preparando los segmentos…"}
        </div>
      )}
      {segmentos && modo !== "unico" && (
        <Segmentos segmentos={segmentos} origen={plan.origen} abierto={abierto} alAbrir={setAbierto}
                   manual={manual} alAnadir={anadirSegmento}
                   alCambiar={cambiarSegmentos} alSugerir={sugerir} estados={estados}
                   catalogo={catalogoEmociones} editable={!generando} voces={voces} avatares={avatares}
                   conVideo={conVideo} animado={animar} emocional={emocional} escenaGeneral={escenaGeneral} />
      )}

      {/* Texto único sin IA: el avatar, su emoción y su animación se configuran aquí */}
      {modo === "unico" && conVideo && segmentos?.[0] && (() => {
        const sg = segmentos[0];
        const cambiarSg = (campo, valor) => cambiarSegmentos([{ ...sg, [campo]: valor }]);
        const avatarSg = avataresUtiles.find((a) => a.id === sg.avatar);
        const vozAvatar = avatarSg?.voz && (voces ?? []).find((v) => v.id === avatarSg.voz)?.nombre;
        return (
          <details className="avanzado ajustes-video" open>
            <summary>Ajustes generales del video{!sg.avatar ? " · ⚠ elige el avatar" : ""}</summary>
            <div className="segmento-meet">
              {sg.avatar
                ? <img className="segmento-vista" alt="Fotograma del video"
                       src={`/api/avatares/${sg.avatar}/vista/${sg.escena || escenaGeneral}`} />
                : <div className="segmento-vista vista-vacia">Elige un avatar</div>}
              <div className="segmento-controles">
                <label><span>Avatar</span>
                  <select className={`selector-avatar ${sg.avatar ? "" : "pendiente"}`} value={sg.avatar ?? ""}
                          disabled={generando} onChange={(e) => cambiarSg("avatar", e.target.value || null)}>
                    <option value="">🧑 Elige el avatar…</option>
                    {avataresUtiles.map((a) => <option key={a.id} value={a.id}>🧑 {a.nombre}</option>)}
                  </select>
                </label>
                <label><span>Voz</span>
                  <select value={sg.voz ?? ""} disabled={generando} onChange={(e) => cambiarSg("voz", e.target.value || null)}>
                    <option value="">{vozAvatar ? `Voz del avatar (${vozAvatar})` : "Voz general"}</option>
                    {(voces ?? []).map((v) => <option key={v.id} value={v.id}>🎙 {v.nombre}</option>)}
                  </select>
                </label>
                <Pista texto="Emoción con la que se dice todo el texto: ajusta el tono, el ritmo, el volumen y sugiere la expresión del avatar." bloque>
                  <label><span>Emoción</span>
                    <select value={sg.emocion} disabled={generando} onChange={(e) => sugerir(0, e.target.value, sg.intensidad)}>
                      {(catalogoEmociones?.emociones ?? [{ id: sg.emocion }]).map((e) => (
                        <option key={e.id} value={e.id}>{etiquetaEmocion(catalogoEmociones, e.id)}</option>
                      ))}
                    </select>
                  </label>
                </Pista>
                <label><span>Intensidad</span>
                  <select value={sg.intensidad} disabled={generando} onChange={(e) => sugerir(0, sg.emocion, e.target.value)}>
                    {(catalogoEmociones?.intensidades ?? ["baja", "media", "alta"]).map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
            {sg.ajustes && (
              <div className="grupo-ajustes">
                <h4>🎭 Animación del avatar</h4>
                <div className="campos campos-avanzados">
                  {AJUSTES_ANIMACION.map((d) => (
                    <Deslizador key={d[0]} def={d} valor={sg.ajustes[d[0]]} sugerido={sg.sugeridos?.[d[0]]}
                                editable={!generando}
                                alCambiar={(k, v) => cambiarSg("ajustes", { ...sg.ajustes, [k]: v })} />
                  ))}
                </div>
              </div>
            )}
          </details>
        );
      })()}

      {/* En manual cada segmento lleva sus propios ajustes de audio */}
      {!manual && (
        <details className="avanzado">
          <summary>Ajustes generales del audio</summary>
          <div className="campos campos-avanzados">
            <Pista texto="Base de la que parten las sugerencias de cada segmento. Baja = estable, alta = expresiva." bloque>
              <label>
                <span>Temperatura <b>{parseFloat(ajustes.temp).toFixed(2)}</b></span>
                <input type="range" min="0.1" max="1.5" step="0.05"
                       value={ajustes.temp} onChange={cambiar("temp")} />
              </label>
            </Pista>
            <Pista texto="Diversidad del muestreo" bloque>
              <label>
                <span>Top-P <b>{parseFloat(ajustes.top_p).toFixed(2)}</b></span>
                <input type="range" min="0.1" max="1" step="0.01"
                       value={ajustes.top_p} onChange={cambiar("top_p")} />
              </label>
            </Pista>
            {CAMPOS_NUMERICOS.map(([campo, etiqueta, ayuda, attrs]) => (
              <Pista key={campo} texto={ayuda} bloque>
                <label>
                  <span>{etiqueta}</span>
                  <input type="number" {...attrs} value={ajustes[campo]} onChange={cambiar(campo)} />
                </label>
              </Pista>
            ))}
          </div>
          <button className="btn btn-sec" onClick={guardarAjustes}>
            {guardado ? "✓ Guardado" : "Guardar como predeterminados"}
          </button>
        </details>
      )}

      <div className="acciones">
        <button className="btn btn-primario btn-grande" disabled={generando || Boolean(motivoBloqueo)}
                onClick={comenzar} title={motivoBloqueo}>
          {generando ? "Generando…" : "▶ Comenzar"}
        </button>
        {generando && <button className="btn btn-peligro" onClick={cancelar}>Cancelar</button>}
        {!generando && motivoBloqueo && limpio && <span className="motivo-bloqueo">{motivoBloqueo}</span>}
        {!generando && !motivoBloqueo && (
          <span className="resumen-comenzar">
            {segmentos.length} segmento{segmentos.length === 1 ? "" : "s"}: audio
            {conVideo ? (animar ? " → video animado" : " → video") : ""} → unión final
          </span>
        )}
      </div>

      {progreso && (
        <div className="progreso">
          <div className="barra"><div style={{ width: `${progreso.pct}%` }}></div></div>
          <span>{progreso.texto}</span>
        </div>
      )}

      {consola !== null && (
        <details ref={cajaConsola} className="consola-caja">
          <summary>Registro del proceso</summary>
          <pre ref={preConsola} className="consola">{consola}</pre>
        </details>
      )}

      {resultado && (
        <div className="resultado">
          <DetalleGeneracion salida={resultado} autoplay />
        </div>
      )}
    </section>
  );
}
