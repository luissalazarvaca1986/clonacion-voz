export default function Aviso({ sistema: s, errorConexion }) {
  if (errorConexion) return <div className="aviso">{errorConexion}</div>;
  if (!s) return null;

  const problemas = [];
  if (!s.binario_ok) {
    problemas.push(<>No se encuentra <code>llama-tts</code>. Instálalo con{" "}
      <code>winget install ggml.llamacpp</code> o indica su ruta en config.json.</>);
  } else if (!s.soporta_qwen3tts) {
    problemas.push(<>Tu build de llama.cpp es anterior al soporte de Qwen3-TTS.
      Actualiza con <code>winget upgrade ggml.llamacpp</code>.</>);
  }
  // Si falta el modelo, el panel de descarga ya lo explica: no duplicamos el aviso.
  if (!s.ffmpeg) {
    problemas.push(<>Sin <code>ffmpeg</code> solo podrás subir referencias en wav o mp3,
      y la grabación desde el navegador no funcionará.</>);
  }
  if (!problemas.length) return null;

  return (
    <div className="aviso">
      {problemas.map((p, i) => <span key={i}>{i > 0 && <br />}{p}</span>)}
    </div>
  );
}
