import { ESCENAS } from "../constantes.js";

// Presentación de cada emoción; los ajustes reales los define el backend (/api/emociones)
export const ESTILO_EMOCION = {
  neutro: { emoji: "😐", color: "#8b96a5" },
  alegria: { emoji: "😊", color: "#f2c94c" },
  entusiasmo: { emoji: "🤩", color: "#f2994a" },
  sorpresa: { emoji: "😮", color: "#56ccf2" },
  tristeza: { emoji: "😢", color: "#5b8def" },
  enojo: { emoji: "😠", color: "#f85149" },
  miedo: { emoji: "😨", color: "#bb6bd9" },
  calma: { emoji: "😌", color: "#3fb950" },
};

export function etiquetaEmocion(catalogo, id) {
  const e = catalogo?.emociones.find((x) => x.id === id);
  return `${ESTILO_EMOCION[id]?.emoji ?? "🎭"} ${e?.etiqueta ?? id}`;
}

// [clave, etiqueta, min, max, paso, formato]
const AJUSTES_AUDIO = [
  ["temp", "Temperatura", 0.1, 1.5, 0.05, (v) => v.toFixed(2)],
  ["top_p", "Top-P", 0.1, 1, 0.01, (v) => v.toFixed(2)],
  ["top_k", "Top-K", 0, 200, 1, (v) => v],
  ["ritmo", "Ritmo", 0.5, 2, 0.01, (v) => `×${v.toFixed(2)}`],
  ["tono", "Tono", -6, 6, 0.1, (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)} st`],
  ["volumen_db", "Volumen", -12, 12, 0.5, (v) => `${v > 0 ? "+" : ""}${v.toFixed(1)} dB`],
  ["pausa_ms", "Pausa después", 0, 3000, 25, (v) => `${v} ms`],
];
export const AJUSTES_ANIMACION = [
  ["escala_expresion", "Expresión", 0, 3, 0.05, (v) => v.toFixed(2)],
  ["estilo_pose", "Estilo de cabeza", 0, 45, 1, (v) => v],
];

const ESTADO_ETAPA = { en_curso: "⏳", listo: "✓" };

export function Deslizador({ def, valor, sugerido, editable, alCambiar }) {
  const [clave, etiqueta, min, max, paso, formato] = def;
  const cambiado = sugerido !== undefined && Number(valor) !== Number(sugerido);
  return (
    <label className={cambiado ? "cambiado" : ""}
           title={cambiado ? `Sugerido: ${formato(Number(sugerido))}` : "Sugerido"}>
      <span>{etiqueta} <b>{formato(Number(valor))}</b>{cambiado && <i> ✎</i>}</span>
      <input type="range" min={min} max={max} step={paso} value={valor} disabled={!editable}
             onChange={(e) => alCambiar(clave, Number(e.target.value))} />
    </label>
  );
}

export function Segmento({ s, i, abierto, alAbrir, ctx }) {
  const { catalogo, editable, voces, avatares, conVideo, animado, emocional, escenaGeneral,
          estado, alCambiarCampo, alCambiarAjuste, alSugerir, textoEditable, alQuitar } = ctx;
  const estilo = ESTILO_EMOCION[s.emocion] ?? ESTILO_EMOCION.neutro;
  const sinAvatar = conVideo && !s.avatar;
  const avatar = (avatares ?? []).find((a) => a.id === s.avatar);
  const vozAvatar = avatar?.voz && (voces ?? []).find((v) => v.id === avatar.voz)?.nombre;
  const nombreVoz = (voces ?? []).find((v) => v.id === s.voz)?.nombre;
  const escena = ESCENAS.find((e) => e.id === (s.escena || escenaGeneral));
  const editados = s.ajustes && s.sugeridos
    ? Object.keys(s.ajustes).filter((k) => Number(s.ajustes[k]) !== Number(s.sugeridos[k])).length : 0;

  return (
    <div className={`segmento ${abierto ? "abierto" : ""} ${sinAvatar ? "falta-avatar" : ""}`}
         style={{ "--color-emocion": estilo.color }}>
      <button className="segmento-cabecera" onClick={alAbrir} aria-expanded={abierto}>
        <span className="segmento-numero">{i + 1}</span>
        <span className={`segmento-texto ${s.texto ? "" : "vacio-texto"}`}>{s.texto || "Segmento sin texto"}</span>
        <span className="segmento-chips">
          {emocional && <span className="chip" title={`${s.emocion} · ${s.intensidad}`}>{estilo.emoji} {s.intensidad}</span>}
          {conVideo && (sinAvatar
            ? <span className="chip chip-aviso">🧑 elige avatar</span>
            : <span className="chip">🧑 {avatar?.nombre ?? s.avatar}</span>)}
          {conVideo && escena && <span className="chip" title={escena.etiqueta}>{escena.emoji}</span>}
          {(nombreVoz || vozAvatar) && <span className="chip">🎙 {nombreVoz ?? vozAvatar}</span>}
          {editados > 0 && <span className="chip" title="Ajustes cambiados por ti">✎ {editados}</span>}
          {estado?.audio && <span className="chip chip-estado">🎙 {ESTADO_ETAPA[estado.audio]}</span>}
          {estado?.video && <span className="chip chip-estado">🎬 {ESTADO_ETAPA[estado.video]}</span>}
        </span>
        <span className="segmento-flecha">{abierto ? "▾" : "▸"}</span>
      </button>

      {abierto && (
        <div className="segmento-cuerpo">
          {textoEditable && (
            <label className="segmento-texto-editable">
              <span>Texto del segmento</span>
              <textarea rows={2} maxLength={5000} value={s.texto} disabled={!editable} autoFocus={!s.texto}
                        placeholder="Lo que se dice en este segmento…"
                        onChange={(e) => alCambiarCampo(i, "texto", e.target.value)} />
            </label>
          )}
          <div className="segmento-controles">
            {emocional && (
              <>
                <label><span>Emoción</span>
                  <select value={s.emocion} disabled={!editable}
                          onChange={(e) => alSugerir(i, e.target.value, s.intensidad)}>
                    {(catalogo?.emociones ?? [{ id: s.emocion }]).map((e) => (
                      <option key={e.id} value={e.id}>{etiquetaEmocion(catalogo, e.id)}</option>
                    ))}
                  </select>
                </label>
                <label><span>Intensidad</span>
                  <select value={s.intensidad} disabled={!editable}
                          onChange={(e) => alSugerir(i, s.emocion, e.target.value)}>
                    {(catalogo?.intensidades ?? ["baja", "media", "alta"]).map((n) => (
                      <option key={n} value={n}>{n}</option>
                    ))}
                  </select>
                </label>
              </>
            )}
            <label><span>Voz</span>
              <select value={s.voz ?? ""} disabled={!editable}
                      onChange={(e) => alCambiarCampo(i, "voz", e.target.value || null)}>
                <option value="">{vozAvatar ? `Voz del avatar (${vozAvatar})` : "Voz general"}</option>
                {(voces ?? []).map((v) => <option key={v.id} value={v.id}>🎙 {v.nombre}</option>)}
              </select>
            </label>
          </div>

          {conVideo && (
            <div className="segmento-meet">
              {s.avatar && escena && (
                <img className="segmento-vista" alt="Fotograma del segmento"
                     src={`/api/avatares/${s.avatar}/vista/${escena.id}`} />
              )}
              <div className="segmento-meet-datos">
                <label><span>Avatar</span>
                  <select className={`selector-avatar ${sinAvatar ? "pendiente" : ""}`} value={s.avatar ?? ""}
                          disabled={!editable} onChange={(e) => alCambiarCampo(i, "avatar", e.target.value || null)}>
                    <option value="">🧑 Elige el avatar…</option>
                    {(avatares ?? []).filter((a) => a.completo).map((a) => (
                      <option key={a.id} value={a.id}>🧑 {a.nombre}</option>
                    ))}
                  </select>
                </label>
                <div><span className="etiqueta-campo">Fondo</span>
                  <div className="selector-escena">
                    {ESCENAS.map((e) => (
                      <button key={e.id} disabled={!editable}
                              className={(s.escena || escenaGeneral) === e.id ? "activa" : ""}
                              onClick={() => alCambiarCampo(i, "escena", e.id)}>{e.emoji} {e.etiqueta}</button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {s.ajustes && (
            <>
              <div className="grupo-ajustes">
                <h4>🎙 Audio {emocional ? "(sugerido por la emoción)" : ""}</h4>
                <div className="campos campos-avanzados">
                  {AJUSTES_AUDIO.map((d) => (
                    <Deslizador key={d[0]} def={d} valor={s.ajustes[d[0]]} sugerido={s.sugeridos?.[d[0]]}
                                editable={editable} alCambiar={(k, v) => alCambiarAjuste(i, k, v)} />
                  ))}
                </div>
              </div>
              {conVideo && animado && (
                <div className="grupo-ajustes">
                  <h4>🎭 Animación del avatar</h4>
                  <div className="campos campos-avanzados">
                    {AJUSTES_ANIMACION.map((d) => (
                      <Deslizador key={d[0]} def={d} valor={s.ajustes[d[0]]} sugerido={s.sugeridos?.[d[0]]}
                                  editable={editable} alCambiar={(k, v) => alCambiarAjuste(i, k, v)} />
                    ))}
                  </div>
                </div>
              )}
              {editados > 0 && (
                <button className="btn-enlace" disabled={!editable}
                        onClick={() => alSugerir(i, s.emocion, s.intensidad)}>↺ Volver a los sugeridos</button>
              )}
            </>
          )}
          {alQuitar && (
            <div className="segmento-pie">
              <button className="btn btn-peligro btn-pequeno" disabled={!editable} onClick={() => alQuitar(i)}>
                🗑 Quitar segmento
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function Segmentos({ segmentos, origen, abierto, alAbrir, alCambiar, alSugerir,
                                    estados, manual, alAnadir, ...resto }) {
  const alCambiarCampo = (i, campo, valor) =>
    alCambiar(segmentos.map((s, j) => (j === i ? { ...s, [campo]: valor } : s)));
  const alCambiarAjuste = (i, clave, valor) =>
    alCambiar(segmentos.map((s, j) => (j === i ? { ...s, ajustes: { ...s.ajustes, [clave]: valor } } : s)));
  const alQuitar = manual && segmentos.length > 1
    ? (i) => { alCambiar(segmentos.filter((_, j) => j !== i)); alAbrir(null); } : null;
  const ctx = { ...resto, alCambiarCampo, alCambiarAjuste, alSugerir, textoEditable: manual, alQuitar };

  return (
    <div className="segmentos">
      <div className="segmentos-cabecera">
        <span>{segmentos.length === 1 ? "1 segmento" : `${segmentos.length} segmentos`} · haz clic para configurar</span>
        <span>{{ llm: "analizado por IA", marcas: "según las marcas del texto", usuario: "revisado",
                 neutro: "IA no disponible: todo neutro", unico: "texto completo",
                 manual: "hechos a mano" }[origen] ?? ""}</span>
      </div>
      {segmentos.map((s, i) => (
        <Segmento key={i} s={s} i={i} abierto={abierto === i}
                  alAbrir={() => alAbrir(abierto === i ? null : i)}
                  ctx={{ ...ctx, estado: estados?.[i + 1] }} />
      ))}
      {manual && (
        <button className="btn btn-sec anadir-segmento" disabled={!resto.editable} onClick={alAnadir}>
          ➕ Añadir segmento
        </button>
      )}
    </div>
  );
}
