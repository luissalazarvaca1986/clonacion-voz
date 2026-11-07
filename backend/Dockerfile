# Backend del clonador de voz: FastAPI + llama-tts con CUDA.
# Requiere nvidia-container-toolkit en el host.
#
#   docker build -t clonar-voz-backend:cuda .                    # imagen de servicio
#   docker build --target pruebas -t clonar-voz-backend:pruebas . && \
#     docker run --rm clonar-voz-backend:pruebas                 # tests
FROM nvidia/cuda:12.8.1-runtime-ubuntu24.04 AS base

ARG LLAMA_BUILD=b11529

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv ffmpeg curl ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# llama.cpp precompilado con CUDA 12.8 (incluye llama-tts con soporte Qwen3-TTS)
RUN mkdir -p /opt/llama.cpp \
    && curl -fsSL "https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_BUILD}/llama-${LLAMA_BUILD}-bin-ubuntu-cuda-12.8-x64.tar.gz" \
       | tar xz --strip-components=1 -C /opt/llama.cpp \
    && ln -s /opt/llama.cpp/llama-tts /usr/local/bin/llama-tts

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CLONAR_BASE=/app \
    CLONAR_DIR_TMP=/tmp/clonar-voz

COPY app ./app

# ---- Tests ----------------------------------------------------------------
FROM base AS pruebas
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY pytest.ini .
COPY tests ./tests
CMD ["pytest", "-q"]

# ---- Servicio (objetivo por defecto) -------------------------------------
FROM base AS servicio
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/salud', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--log-level", "warning", "--timeout-graceful-shutdown", "5"]
