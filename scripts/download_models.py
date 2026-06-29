"""Download SmartDigest local ML models into the image build cache."""

import os

from sentence_transformers import CrossEncoder, SentenceTransformer


def _as_bool(value: str, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def main() -> None:
    semantic_model = os.environ.get(
        "SEMANTIC_MODEL_NAME",
        "sentence-transformers/all-MiniLM-L6-v2",
    )
    reranker_model = os.environ.get(
        "RERANKER_MODEL_NAME",
        "cross-encoder/ettin-reranker-68m-v1",
    )
    sentence_transformers_home = os.environ.get("SENTENCE_TRANSFORMERS_HOME")

    if _as_bool(os.environ.get("DOWNLOAD_SEMANTIC_MODEL", "true")):
        SentenceTransformer(
            semantic_model,
            cache_folder=sentence_transformers_home,
            local_files_only=False,
        )
        print(f"Downloaded semantic model: {semantic_model}")

    if _as_bool(os.environ.get("DOWNLOAD_RERANKER_MODEL", "true")):
        CrossEncoder(
            reranker_model,
            cache_folder=sentence_transformers_home,
            local_files_only=False,
        )
        print(f"Downloaded reranker model: {reranker_model}")


if __name__ == "__main__":
    main()
