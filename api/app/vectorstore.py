import os
from typing import List
from langchain_community.vectorstores import Milvus as LC_Milvus
from langchain.docstore.document import Document

EMBEDDINGS_KIND = os.getenv("EMBEDDINGS_KIND", "local").lower()

def _get_embedding_fn():
    if EMBEDDINGS_KIND == "openai":
        from langchain_community.embeddings import OpenAIEmbeddings
        model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        return OpenAIEmbeddings(model=model)
    else:
        # local sentence-transformers
        from sentence_transformers import SentenceTransformer
        _model = os.getenv("EMBEDDINGS_MODEL", "BAAI/bge-small-en-v1.5")
        st = SentenceTransformer(_model)
        def _embed_documents(texts: List[str]):
            return st.encode(texts, normalize_embeddings=True).tolist()
        class _LocalEmb:
            def embed_documents(self, texts): return _embed_documents(texts)
            def embed_query(self, text): return _embed_documents([text])[0]
        return _LocalEmb()

def get_store():
    host = os.getenv("MILVUS_HOST", "milvus")
    port = os.getenv("MILVUS_PORT", "19530")
    collection = os.getenv("MILVUS_COLLECTION", "company_kb")
    emb = _get_embedding_fn()
    store = LC_Milvus(
        connection_args={"host": host, "port": port},
        collection_name=collection,
        embedding_function=emb
    )
    return store

def upsert_texts(pairs: List[tuple]):
    """
    pairs: list of (text, metadata_dict)
    """
    texts = [t for t, _ in pairs]
    metadatas = [m for _, m in pairs]
    host = os.getenv("MILVUS_HOST", "milvus")
    port = os.getenv("MILVUS_PORT", "19530")
    collection = os.getenv("MILVUS_COLLECTION", "company_kb")
    emb = _get_embedding_fn()
    LC_Milvus.from_texts(
        texts=texts,
        embedding=emb,
        metadatas=metadatas,
        collection_name=collection,
        connection_args={"host": host, "port": port},
    )

def retrieve(query: str, k: int = 4) -> List[Document]:
    return get_store().similarity_search(query, k=k)
