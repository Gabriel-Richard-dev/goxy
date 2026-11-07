# Goxy — imagem única: painel React compilado + FastAPI (sistemas simulados, orquestrador LangGraph, canais)
FROM node:20-slim AS web
WORKDIR /web
COPY goxys/web/package.json goxys/web/package-lock.json* ./
RUN npm ci --no-audit --no-fund || npm install --no-audit --no-fund
COPY goxys/web/ ./
RUN npx tsc -b && npx vite build --outDir dist --emptyOutDir

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY goxys/requirements.txt goxys/requirements.txt
RUN pip install -r goxys/requirements.txt
COPY GoHacks_Talent_2026_bases_csv.zip /tmp/bases.zip
RUN python -c "import zipfile; zipfile.ZipFile('/tmp/bases.zip').extractall('/app/data_raw')" && rm /tmp/bases.zip
COPY assets/ assets/
COPY SPEC.md ./
COPY goxys/ goxys/
COPY --from=web /web/dist goxys/web/dist
WORKDIR /app/goxys
RUN python data/build_db.py
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
