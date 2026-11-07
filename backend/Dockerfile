# Backend del clonador de voz: FastAPI + llama-tts con CUDA.
# Requiere nvidia-container-toolkit en el host.
#
#   docker build -t clonar-voz-backend:cuda .                    # imagen de servicio
#   docker build --target pruebas -t clonar-voz-backend:pruebas . && \
#     docker run --rm clonar-voz-backend:pruebas                 # tests
# llama.cpp se compila desde el código fuente: no hay binarios oficiales con CUDA
# para ARM64 (DGX Spark / GB10).
# CUDA_ARCHITECTURES: 121 = GB10 (DGX Spark, Blackwell).
ARG CUDA_VERSION=13.0.1
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu24.04 AS llama

ARG LLAMA_BUILD=b11529
ARG CUDA_ARCHITECTURES=121

RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
        git cmake build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch ${LLAMA_BUILD} https://github.com/ggml-org/llama.cpp.git /src \
    && cmake -S /src -B /src/build \
         -DCMAKE_BUILD_TYPE=Release \
         -DGGML_CUDA=ON \
         -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}" \
         -DGGML_NATIVE=OFF \
         -DBUILD_SHARED_LIBS=OFF \
         -DLLAMA_CURL=OFF \
         -DLLAMA_BUILD_TESTS=OFF \
         -DCMAKE_EXE_LINKER_FLAGS="-Wl,-rpath-link,/usr/local/cuda/lib64/stubs -L/usr/local/cuda/lib64/stubs" \
    && cmake --build /src/build --config Release -j"$(nproc)" --target llama-tts llama-server \
    && mkdir -p /opt/llama.cpp \
    && cp /src/build/bin/llama-tts /src/build/bin/llama-server /opt/llama.cpp/

FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu24.04 AS base

RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
        python3 python3-venv ffmpeg curl ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=llama /opt/llama.cpp /opt/llama.cpp
RUN ln -s /opt/llama.cpp/llama-tts /usr/local/bin/llama-tts

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
