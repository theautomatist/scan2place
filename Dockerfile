FROM python:3.12-slim

WORKDIR /app

# Abhaengigkeiten zuerst (Docker-Layer-Caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Anwendungscode
COPY app/ ./app/
COPY static/ ./static/
COPY templates/ ./templates/

# HTTPS-Port (self-signed Zertifikat wird beim Start erzeugt)
EXPOSE 8090

# data/ enthaelt hochgeladene iBOMs, Zustaende und das Zertifikat (per Volume gemountet)
VOLUME ["/app/data"]

CMD ["python", "-m", "app.main"]
