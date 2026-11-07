# -*- coding: utf-8 -*-
"""Servidor HTTP mínimo que envuelve el inference.py de SadTalker.

Corre en su propio contenedor (Python 3.8 + torch 1.12.1) y solo usa la
biblioteca estándar para no añadir dependencias al entorno de SadTalker.

    GET  /salud         {"ok": true, "gpu": bool, "ocupado": bool}
    GET  /capacidades   lo que este motor sabe hacer de verdad
    POST /animar        JSON -> flujo NDJSON: {"tipo": "log"|"fin"|"error", ...}

Cada animación lanza inference.py como proceso aparte: al terminar se libera
toda la memoria (VRAM incluida). Solo se atiende una animación a la vez.
Si el cliente corta la conexión, el proceso se mata (cancelación).

Las rutas de entrada y salida son relativas a RAIZ (volumen compartido con el
backend) y no pueden salir de ella.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(os.environ.get("ANIMADOR_RAIZ", "/trabajo")).resolve()
MODELOS = Path(os.environ.get("ANIMADOR_MODELOS", "/modelos"))
SADTALKER = Path("/opt/sadtalker")
TIMEOUT = int(os.environ.get("ANIMADOR_TIMEOUT", "3600"))
PUERTO = int(os.environ.get("ANIMADOR_PUERTO", "8080"))

# Animaciones simultáneas: 1 en GPUs pequeñas; más en equipos con memoria de sobra
# (cada una es un proceso SadTalker independiente que libera su memoria al terminar)
CONCURRENCIA = max(1, int(os.environ.get("ANIMADOR_CONCURRENCIA", "1")))
CERROJO = threading.BoundedSemaphore(CONCURRENCIA)
_activas = [0]
_cuenta = threading.Lock()


def _ocupado():
    with _cuenta:
        return _activas[0] >= CONCURRENCIA
_PATRON_OOM = re.compile(r"CUDA out of memory|CUBLAS_STATUS_ALLOC_FAILED|out of memory", re.I)

CAPACIDADES = {
    "motor": "sadtalker",
    "nombre": "SadTalker",
    "version": "v0.0.2 (commit cd4c046)",
    "nativo": {
        "labios": True,          # sincronía labial a partir del audio
        "parpadeo": True,        # automático, sin control de frecuencia
        "pose_cabeza": True,     # 46 estilos predefinidos
        "expresiones": True,     # una sola escala global
        "mirada": False,         # no se controla por separado
        "transparencia": False,  # el MP4 sale sin canal alfa
    },
    "opciones": {
        "tamano": {"tipo": "enum", "valores": [256], "defecto": 256,
                   "ayuda": "Resolución del render facial. 512 no cabe en 4 GB."},
        "estilo_pose": {"tipo": "entero", "min": 0, "max": 45, "defecto": 0,
                        "ayuda": "Estilo de movimiento de cabeza (0-45)."},
        "escala_expresion": {"tipo": "decimal", "min": 0.0, "max": 3.0, "defecto": 1.0,
                             "ayuda": "Intensidad de las expresiones y la boca."},
        "quieto": {"tipo": "booleano", "defecto": True,
                   "ayuda": "Cabeza casi fija: necesario para conservar el encuadre "
                            "y la transparencia del avatar completo."},
        "preproceso": {"tipo": "enum", "valores": ["full", "crop"], "defecto": "full",
                       "ayuda": "full = imagen completa; crop = solo la cara (256×256)."},
        "dispositivo": {"tipo": "enum", "valores": ["gpu", "cpu"], "defecto": "gpu"},
    },
    "formatos_salida": ["mp4"],
}


def _hay_gpu():
    return Path("/dev/nvidia0").exists() or Path("/dev/nvidiactl").exists()


def _ruta_segura(relativa):
    ruta = (RAIZ / str(relativa)).resolve()
    if RAIZ not in ruta.parents and ruta != RAIZ:
        raise ValueError("Ruta fuera del área de trabajo: %s" % relativa)
    return ruta


def _comando(p, imagen, audio, resultados):
    preproceso = p.get("preproceso", "full")
    if preproceso not in ("full", "crop"):
        raise ValueError("preproceso no válido")
    cmd = ["python", "inference.py",
           "--driven_audio", str(audio), "--source_image", str(imagen),
           "--result_dir", str(resultados), "--checkpoint_dir", str(MODELOS / "checkpoints"),
           "--size", "256", "--preprocess", preproceso,
           "--pose_style", str(min(45, max(0, int(p.get("estilo_pose", 0))))),
           "--expression_scale", "%.2f" % min(3.0, max(0.0, float(p.get("escala_expresion", 1.0)))),
           "--batch_size", "1"]   # lote 1: el mínimo de VRAM en 4 GB
    if p.get("quieto", True):
        cmd.append("--still")
    if p.get("dispositivo") == "cpu" or not _hay_gpu():
        cmd.append("--cpu")
    return cmd


class Manejador(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, formato, *args):  # registro compacto
        print("%s %s" % (self.address_string(), formato % args), flush=True)

    def _json(self, codigo, datos):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        if self.path == "/salud":
            self._json(200, {"ok": True, "gpu": _hay_gpu(), "ocupado": _ocupado(), "concurrencia": CONCURRENCIA})
        elif self.path == "/capacidades":
            self._json(200, dict(CAPACIDADES, gpu=_hay_gpu()))
        else:
            self._json(404, {"error": "no encontrado"})

    def _evento(self, datos):
        linea = (json.dumps(datos, ensure_ascii=False) + "\n").encode()
        self.wfile.write(b"%x\r\n%s\r\n" % (len(linea), linea))   # chunked
        self.wfile.flush()

    def do_POST(self):
        if self.path != "/animar":
            return self._json(404, {"error": "no encontrado"})
        try:
            p = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            imagen, audio = _ruta_segura(p["imagen"]), _ruta_segura(p["audio"])
            salida = _ruta_segura(p["salida"])
            if not imagen.is_file() or not audio.is_file():
                raise ValueError("No existe la imagen o el audio")
        except (KeyError, ValueError, TypeError) as exc:
            return self._json(400, {"error": str(exc)})
        if not CERROJO.acquire(blocking=False):
            return self._json(409, {"error": "Ya hay %d animación(es) en curso" % CONCURRENCIA})
        with _cuenta:
            _activas[0] += 1

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        resultados = Path(tempfile.mkdtemp(prefix="sadtalker-"))
        proceso = None
        inicio = time.time()
        try:
            cmd = _comando(p, imagen, audio, resultados)
            self._evento({"tipo": "log", "linea": "SadTalker en %s" % (
                "CPU" if "--cpu" in cmd else "GPU")})
            proceso = subprocess.Popen(cmd, cwd=SADTALKER, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT)
            ultimas, trozo = [], b""
            # tqdm reescribe la línea con \r: se trata como un salto más
            while True:
                byte = proceso.stdout.read(1)
                if not byte:
                    break
                if byte in (b"\n", b"\r"):
                    linea = trozo.decode("utf-8", "replace").strip()
                    trozo = b""
                    if linea:
                        ultimas = (ultimas + [linea])[-30:]
                        self._evento({"tipo": "log", "linea": linea})
                else:
                    trozo += byte
                if time.time() - inicio > TIMEOUT:
                    proceso.kill()
                    raise RuntimeError("La animación superó el tiempo máximo (%d s)" % TIMEOUT)
            codigo = proceso.wait()
            videos = sorted(resultados.glob("*.mp4"))
            if codigo != 0 or not videos:
                texto = "\n".join(ultimas[-8:])
                self._evento({"tipo": "error", "oom": bool(_PATRON_OOM.search(texto)),
                              "mensaje": "SadTalker terminó con código %s: %s" % (codigo, texto[-600:])})
                return
            salida.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(videos[-1]), str(salida))
            self._evento({"tipo": "fin", "video": str(salida.relative_to(RAIZ)),
                          "segundos": round(time.time() - inicio, 1)})
        except (BrokenPipeError, ConnectionResetError):
            print("Cliente desconectado: se cancela la animación", flush=True)
        except Exception as exc:  # noqa: BLE001 — se informa al backend
            try:
                self._evento({"tipo": "error", "oom": False, "mensaje": str(exc)})
            except OSError:
                pass
        finally:
            if proceso and proceso.poll() is None:
                proceso.kill()
                proceso.wait()
            shutil.rmtree(resultados, ignore_errors=True)
            with _cuenta:
                _activas[0] -= 1
            CERROJO.release()
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except OSError:
                pass


if __name__ == "__main__":
    print("Animador SadTalker en :%d · raíz %s · GPU %s" % (PUERTO, RAIZ, _hay_gpu()), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PUERTO), Manejador).serve_forever()
