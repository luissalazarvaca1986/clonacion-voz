# Transcripción de voz (speech-to-text) con whisper.cpp, en CPU.
# Equipo de desarrollo (GTX 1650, 4 GB): la VRAM queda libre para Qwen3-TTS y SadTalker,
# así que Whisper corre en CPU con un modelo pequeño (ggml-small por defecto).
# En producción (DGX Spark / GB10) la rama `gb10` compila esta imagen con CUDA.
FROM ubuntu:24.04 AS compilacion

ARG WHISPER_VERSION=v1.9.5

RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 install -y --no-install-recommends \
        git cmake build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# GGML_NATIVE=OFF + CPU_ALL_VARIANTS: binario portable que elige AVX2/AVX512 al arrancar
RUN git clone --depth 1 --branch ${WHISPER_VERSION} https://github.com/ggml-org/whisper.cpp.git /src \
    && cmake -S /src -B /src/build \
         -DCMAKE_BUILD_TYPE=Release \
         -DGGML_NATIVE=OFF \
         -DGGML_BACKEND_DL=ON \
         -DGGML_CPU_ALL_VARIANTS=ON \
         -DBUILD_SHARED_LIBS=ON \
         -DWHISPER_BUILD_TESTS=OFF \
    && cmake --build /src/build --config Release -j"$(nproc)" --target whisper-server \
    && mkdir -p /opt/whisper \
    && cp /src/build/bin/whisper-server /opt/whisper/ \
    && find /src/build -name '*.so*' -exec cp -P {} /opt/whisper/ \;

FROM ubuntu:24.04
RUN apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 update && apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=30 install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=compilacion /opt/whisper /opt/whisper
ENV LD_LIBRARY_PATH=/opt/whisper
RUN ln -s /opt/whisper/whisper-server /usr/local/bin/whisper-server
WORKDIR /opt/whisper
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fs http://127.0.0.1:8080/ >/dev/null || exit 1
# Siempre en español; el modelo se monta en /modelos
CMD ["whisper-server", "-m", "/modelos/ggml-small.bin", "-l", "es", \
     "--host", "0.0.0.0", "--port", "8080", "-t", "4"]
