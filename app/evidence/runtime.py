"""Optional local retrieval resources for the API; no downloads at startup."""

from contextlib import contextmanager
from functools import lru_cache
import os
from pathlib import Path
from threading import RLock

from app.core.paths import ROOT
_lock = RLock()


class EvidenceUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=2)
def _embedder(cache: str):
    from app.evidence.retrieval import LocalEmbedder
    return LocalEmbedder(Path(cache), offline=True)


@contextmanager
def open_evidence_index():
    """Serialize local-store access, close clients, and never download models.

    This lock covers only this process. Local Qdrant is for a single API worker;
    use server-mode Qdrant before deploying multiple workers.
    """
    storage = Path(os.environ.get("DEBATEBOT_EVIDENCE_STORAGE", str(ROOT / ".data/qdrant")))
    cache = Path(os.environ.get("DEBATEBOT_EMBEDDING_CACHE", str(ROOT / ".data/models")))
    with _lock:
        client = None
        try:
            from qdrant_client import QdrantClient
            from app.evidence.retrieval import EvidenceIndex
            from app.evidence.relevance import RelevantLocalIndex
            if not storage.is_dir():
                raise EvidenceUnavailable("Index corpus sources before using evidence analysis")
            encoder = _embedder(str(cache.resolve()))
            client = QdrantClient(path=str(storage))
            index = EvidenceIndex(client, encoder)
            if not client.collection_exists(index.collection):
                raise EvidenceUnavailable("Index is missing for the current embedding model")
        except (ImportError, OSError, RuntimeError, ValueError) as error:
            if client is not None:
                client.close()
            raise EvidenceUnavailable("Evidence index unavailable; install retrieval dependencies and run python -m app.evidence.retrieval index") from error
        try:
            yield RelevantLocalIndex(index)
        finally:
            client.close()
