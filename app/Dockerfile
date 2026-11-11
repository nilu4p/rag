# Service RAG : API + interface de chat + extraction (OCR Tesseract, pandoc, catdoc)
FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng pandoc catdoc ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pytest

COPY rag ./rag
# Utilisateur non root, même UID que l'utilisateur du PC (fichiers de documents/ lisibles et modifiables)
ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} rag 2>/dev/null || true \
 && useradd -u ${UID} -g ${GID} -M -d /app rag 2>/dev/null || true \
 && mkdir -p /data/state /data/documents && chown -R ${UID}:${GID} /data
USER ${UID}:${GID}

ENV PYTHONUNBUFFERED=1 OMP_THREAD_LIMIT=4
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=4).status==200 else 1)"
CMD ["uvicorn", "rag.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "75"]
