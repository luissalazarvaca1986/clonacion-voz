# -*- coding: utf-8 -*-
"""Genera avatares/<id>/rostro.json: los puntos de la cara que usa la «Simulación de
movimiento» (animación ligera en el navegador, sin IA de vídeo).

Se ejecuta dentro del contenedor del animador, que ya tiene facexlib y sus pesos
(RetinaFace + AWing, 98 puntos WFLW), en CPU:

    curl -s -o cache/animacion/vista-<ID>.png localhost:8093/api/avatares/<ID>/vista/dia
    docker exec -i clonar-voz-animador python - /trabajo/vista-<ID>.png < herramientas/rostro.py \\
        > avatares/<ID>/rostro.json

La imagen es la vista compuesta del avatar sobre su fondo (1280×720). Los puntos valen
para todos los fondos mientras no cambie la posición del avatar (se guarda en el archivo
y la web la compara).
"""
import json
import sys
import time
import warnings

warnings.filterwarnings("ignore")

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from facexlib.alignment import init_alignment_model  # noqa: E402
from facexlib.detection import init_detection_model  # noqa: E402

PESOS = "/modelos/gfpgan/weights"
MARGEN = 0.25          # recorte cuadrado: la caja de la cara + 25 % por cada lado

# Grupos de la anotación WFLW de 98 puntos
GRUPOS = {
    "contorno": range(0, 33),        # de la sien izquierda a la derecha pasando por la barbilla
    "ceja_izq": range(33, 42),
    "ceja_der": range(42, 51),
    "nariz": range(51, 60),
    "ojo_izq": range(60, 68),        # 8 puntos desde el lagrimal exterior, por arriba
    "ojo_der": range(68, 76),
    "boca_ext": range(76, 88),       # 12 puntos desde la comisura izquierda, por arriba
    "boca_int": range(88, 96),       # 8 puntos desde la comisura interior izquierda, por arriba
    "pupilas": range(96, 98),
}


def main(ruta: str) -> None:
    img = cv2.imread(ruta, cv2.IMREAD_COLOR)
    if img is None:
        sys.exit(f"No se puede leer {ruta}")
    alto, ancho = img.shape[:2]
    detector = init_detection_model("retinaface_resnet50", device="cpu", model_rootpath=PESOS)
    alineador = init_alignment_model("awing_fan", device="cpu", model_rootpath=PESOS)
    with torch.no_grad():
        caras = detector.detect_faces(img, 0.9)
    if len(caras) == 0:
        sys.exit("No se detectó ninguna cara en la imagen")
    x1, y1, x2, y2, confianza = max(caras, key=lambda c: (c[2] - c[0]) * (c[3] - c[1]))[:5]

    lado = max(x2 - x1, y2 - y1) * (1 + 2 * MARGEN)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    ox, oy = int(round(cx - lado / 2)), int(round(cy - lado / 2))
    lado = int(round(lado))
    # Recorte con relleno si se sale de la imagen
    lienzo = np.zeros((lado, lado, 3), np.uint8)
    sx0, sy0 = max(0, ox), max(0, oy)
    sx1, sy1 = min(ancho, ox + lado), min(alto, oy + lado)
    lienzo[sy0 - oy:sy1 - oy, sx0 - ox:sx1 - ox] = img[sy0:sy1, sx0:sx1]
    with torch.no_grad():
        puntos = alineador.get_landmarks(lienzo)
    puntos = puntos + np.array([ox, oy])

    redondear = lambda p: [round(float(p[0]), 1), round(float(p[1]), 1)]  # noqa: E731
    json.dump({
        "version": 1,
        "formato": "wflw98",
        "imagen": {"ancho": ancho, "alto": alto},
        "caja": [round(float(v), 1) for v in (x1, y1, x2, y2)],
        "confianza": round(float(confianza), 3),
        "grupos": {nombre: [redondear(puntos[i]) for i in rango] for nombre, rango in GRUPOS.items()},
        "generado": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main(sys.argv[1])
