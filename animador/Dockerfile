# Motor de animación facial (SadTalker) aislado del backend: necesita Python 3.8
# y PyTorch 1.12.1 + CUDA 11.3, incompatibles con el Python 3.12 del backend.
FROM python:3.8-slim-bookworm

ARG SADTALKER_COMMIT=cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg git libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Código de SadTalker fijado a un commit concreto (el repositorio no cambia desde 2023)
RUN git clone https://github.com/OpenTalker/SadTalker.git /opt/sadtalker \
    && git -C /opt/sadtalker checkout ${SADTALKER_COMMIT} \
    && rm -rf /opt/sadtalker/.git /opt/sadtalker/examples /opt/sadtalker/docs

# torch con CUDA 11.3: incluye kernels para la GTX 1650 (sm_75)
RUN pip install --no-cache-dir torch==1.12.1+cu113 torchvision==0.13.1+cu113 torchaudio==0.12.1 \
        --extra-index-url https://download.pytorch.org/whl/cu113
COPY requisitos-sadtalker.txt /tmp/
# Sin aislamiento de compilación: basicsr compila contra el torch ya instalado
RUN pip install --no-cache-dir numpy==1.23.4 \
    && pip install --no-cache-dir --no-build-isolation -r /tmp/requisitos-sadtalker.txt

# Los pesos viven en un volumen (./modelos-animacion), fuera de la imagen y del repo
RUN mkdir -p /opt/sadtalker/gfpgan \
    && ln -s /modelos/gfpgan/weights /opt/sadtalker/gfpgan/weights

# SadTalker escribe temporales en su carpeta de trabajo: el usuario sin privilegios
# (1000) también debe poder escribir ahí. HOME aloja las cachés de torch/facexlib.
RUN chmod 1777 /opt/sadtalker && mkdir -p /tmp/animador && chmod 1777 /tmp/animador

COPY servidor.py /opt/animador/servidor.py
WORKDIR /opt/sadtalker
ENV PYTHONUNBUFFERED=1 \
    HOME=/tmp/animador
EXPOSE 8080
CMD ["python", "/opt/animador/servidor.py"]
