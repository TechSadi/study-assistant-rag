"""The vector store: document chunks and their embeddings, in one ChromaDB collection.

This is the only module that talks to ChromaDB. rag.py builds the chunks and the
filters; replacing ChromaDB (with pgvector, say) means reimplementing these few
functions and translating the filter format below.

Each chunk has the id `{doc_id}_{position}` and the metadata
{user_id, doc_id, source, page}. Filters are ChromaDB `where` clauses built by
rag.scope_filter, which always include the owner's user_id.

The collection is a cache of what can be rebuilt from the stored files (see
main.recover_index), so it's fine for it to live on a disk that isn't durable.
"""

import functools
import logging
from functools import lru_cache

import chromadb

from . import config

log = logging.getLogger("skimmi.vectors")


class VectorStoreError(Exception):
    """The vector store failed. The API answers 503."""


def _store_call(fn):
    """Any failure inside the vector store becomes VectorStoreError (logged here)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except VectorStoreError:
            raise
        except Exception as e:
            log.exception("Vector store %s failed", fn.__name__)
            raise VectorStoreError() from e
    return wrapper


@lru_cache(maxsize=1)
def get_collection():
    log.info("Opening ChromaDB at %s", config.CHROMA_DIR)
    db = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    return db.get_or_create_collection(
        name=config.COLLECTION_NAME,
        configuration={"hnsw": {"space": "cosine"}},
    )


@_store_call
def add(ids, texts, embeddings, metadatas):
    get_collection().add(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)


@_store_call
def delete_document(doc_id):
    get_collection().delete(where={"doc_id": doc_id})


@_store_call
def count(where=None):
    """How many chunks match the filter (all chunks without one)."""
    if where is None:
        return get_collection().count()
    return len(get_collection().get(where=where, include=[])["ids"])


@_store_call
def has_document(doc_id):
    return bool(get_collection().get(where={"doc_id": doc_id}, limit=1, include=[])["ids"])


@_store_call
def get(where):
    """Every matching chunk as (id, text, metadata)."""
    got = get_collection().get(where=where, include=["documents", "metadatas"])
    return list(zip(got["ids"], got["documents"], got["metadatas"]))


@_store_call
def query(embedding, n, where):
    """The n chunks nearest the embedding, nearest first, as (text, metadata, distance)."""
    results = get_collection().query(
        query_embeddings=[embedding],
        n_results=n,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    return list(zip(results["documents"][0], results["metadatas"][0], results["distances"][0]))


@_store_call
def document_ids():
    """The distinct doc_ids that have chunks. Reads every chunk's metadata, so it
    is only used by the startup recovery, to clean up after deleted accounts."""
    got = get_collection().get(include=["metadatas"])
    return {m.get("doc_id") for m in got["metadatas"]}
