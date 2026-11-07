# Transcripción de voz (speech-to-text) con whisper.cpp + CUDA.
# Se compila desde el código fuente para ARM64 (DGX Spark / GB10).
#   CUDA_ARCHITECTURES: 121 = GB10 (DGX Spark, Blackwell)
ARG CUDA_VERSION=13.0.1
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu24.04 AS compilacion

ARG WHISPER_VERSION=v1.9.5
ARG CUDA_ARCHITECTURES=121

RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
        git cmake build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch ${WHISPER_VERSION} https://github.com/ggml-org/whisper.cpp.git /src \
    && cmake -S /src -B /src/build \
         -DCMAKE_BUILD_TYPE=Release \
         -DGGML_CUDA=ON \
         -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}" \
         -DGGML_NATIVE=OFF \
         -DBUILD_SHARED_LIBS=OFF \
         -DWHISPER_BUILD_TESTS=OFF \
         -DCMAKE_EXE_LINKER_FLAGS="-Wl,-rpath-link,/usr/local/cuda/lib64/stubs -L/usr/local/cuda/lib64/stubs" \
    && cmake --build /src/build --config Release -j"$(nproc)" --target whisper-server \
    && cp /src/build/bin/whisper-server /usr/local/bin/

FROM nvidia/cuda:${CUDA_VERSION}-runtime-ubuntu24.04
RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=compilacion /usr/local/bin/whisper-server /usr/local/bin/whisper-server
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fs http://127.0.0.1:8080/ >/dev/null || exit 1
# Siempre en español; el modelo se monta en /modelos
CMD ["whisper-server", "-m", "/modelos/ggml-large-v3-turbo.bin", "-l", "es", \
     "--host", "0.0.0.0", "--port", "8080", "-t", "8"]
