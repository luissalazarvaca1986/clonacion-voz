# Asistente por voz con avatar: web (puerto 8080) + orquestación STT → RAG → LLM → vídeo
FROM python:3.12-slim
WORKDIR /app
# ffmpeg comprime los clips de voz (WAV → AAC, ~8 veces menos) para el celular por Wi-Fi
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py ajustes.py ./
COPY static ./static
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=4)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
