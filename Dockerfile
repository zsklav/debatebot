FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face Spaces runs containers as uid 1000. The app writes its local Qdrant
# store and embedding cache under /app/.data, so that user must own /app.
RUN useradd -m -u 1000 user && mkdir /app && chown user:user /app
USER user
ENV PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY --chown=user:user requirements.txt .
COPY --chown=user:user requirements/ requirements/
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY --chown=user:user . .

# The application never downloads models at runtime. Fetch the embedding model and
# index the small study-summary corpus now; the build fails here if either step fails.
RUN python -m app.evidence.retrieval index

# Don't bake secrets into the image. .env is excluded by .dockerignore; set keys
# as runtime secrets in your deploy platform (for example Hugging Face Spaces secrets).
# Without DATABASE_URL, accounts and reports are volatile and vanish on restart.
EXPOSE 8080

# One worker only: the local Qdrant store is not safe for several processes.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
