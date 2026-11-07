# Motor de animación facial (SadTalker) aislado del backend, para DGX Spark (GB10, ARM64).
# torch 1.12.1+cu113 no existe para aarch64 ni soporta Blackwell, así que aquí
# se usa Python 3.10 + PyTorch con CUDA 13.0 y se parchean las dos
# incompatibilidades de SadTalker/basicsr con torch moderno.
FROM python:3.10-slim-bookworm

ARG SADTALKER_COMMIT=cd4c0465ae0b54a6f85af57f5c65fec9fe23e7f8
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu130

RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
        ffmpeg git libgl1 libglib2.0-0 build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/OpenTalker/SadTalker.git /opt/sadtalker \
    && git -C /opt/sadtalker checkout ${SADTALKER_COMMIT} \
    && rm -rf /opt/sadtalker/.git /opt/sadtalker/examples /opt/sadtalker/docs

# El pip de la imagen base es demasiado viejo para el índice de PyTorch
RUN pip install --no-cache-dir --upgrade pip "setuptools<81" wheel \
    && pip install --no-cache-dir torch==2.9.1 torchvision==0.24.1 torchaudio==2.9.1 \
        --index-url ${TORCH_INDEX} --extra-index-url https://pypi.org/simple

COPY requisitos-sadtalker.txt /tmp/
RUN pip install --no-cache-dir numpy==1.23.5 \
    && pip install --no-cache-dir --no-build-isolation -r /tmp/requisitos-sadtalker.txt

# basicsr importa torchvision.transforms.functional_tensor, eliminado en torchvision ≥ 0.17
RUN f=$(python -c "import site;print(site.getsitepackages()[0])")/basicsr/data/degradations.py \
    && sed -i 's/torchvision.transforms.functional_tensor/torchvision.transforms.functional/' "$f" \
    && python -c "import basicsr, gfpgan, facexlib, face_alignment, kornia, torch; print('ok', torch.__version__)"

RUN mkdir -p /opt/sadtalker/gfpgan \
    && ln -s /modelos/gfpgan/weights /opt/sadtalker/gfpgan/weights

RUN chmod 1777 /opt/sadtalker && mkdir -p /tmp/animador && chmod 1777 /tmp/animador

COPY servidor.py /opt/animador/servidor.py
WORKDIR /opt/sadtalker
# torch ≥ 2.6 carga con weights_only=True por defecto; los checkpoints de SadTalker
# (pickles completos) lo necesitan desactivado.
ENV PYTHONUNBUFFERED=1 \
    HOME=/tmp/animador \
    TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1
EXPOSE 8080
CMD ["python", "/opt/animador/servidor.py"]
