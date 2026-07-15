from __future__ import annotations

import argparse
import gc
import hashlib
import ipaddress
import json
import shutil
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import fitz
import requests


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.ai.ingestion import ExtractedSection, chunk_sections, normalize_text  # noqa: E402
from app.config import Config  # noqa: E402


MANIFEST_PATH = BACKEND_DIR / "rag_sources" / "manifest.json"
RUNTIME_DIR = Path(Config.RAG_RUNTIME_PATH)
STATE_PATH = RUNTIME_DIR / "state.json"
MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 120


def _json(path: Path, fallback):
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _validate_remote_url(url: str, expected_host: str | None = None):
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        raise ValueError("RAG sources must use HTTPS")
    if expected_host and parsed.hostname.lower() != expected_host.lower():
        raise ValueError("RAG redirect crossed the approved host boundary")
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("RAG source hostname did not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0].split("%")[0])
        if not ip.is_global:
            raise ValueError("RAG source resolved to a non-public address")
    return parsed.hostname


def _download(url: str) -> bytes:
    approved_host = _validate_remote_url(url)
    current = url
    with requests.Session() as session:
        for _redirect in range(4):
            response = session.get(
                current,
                headers={"User-Agent": "HealthDoc-RAG-Sync/1.0"},
                timeout=(5, 30),
                stream=True,
                allow_redirects=False,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                target = urljoin(current, response.headers.get("Location") or "")
                _validate_remote_url(target, approved_host)
                current = target
                response.close()
                continue
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "pdf" not in content_type and not current.lower().endswith(".pdf"):
                raise ValueError(f"unexpected content type: {content_type}")
            output = bytearray()
            for part in response.iter_content(64 * 1024):
                output.extend(part)
                if len(output) > MAX_BYTES:
                    raise ValueError("RAG source exceeds 20 MiB")
            return bytes(output)
    raise ValueError("too many redirects")


def _extract_pdf(data: bytes) -> list[ExtractedSection]:
    document = fitz.open(stream=data, filetype="pdf")
    if document.page_count > MAX_PAGES:
        raise ValueError(f"PDF exceeds {MAX_PAGES} pages")
    sections = []
    for index, page in enumerate(document):
        text = page.get_text("text").strip()
        if text:
            sections.append(ExtractedSection(locator=f"第 {index + 1} 页", text=text))
    if not sections:
        # Some approved NHC standards are image-only scans. This fallback is
        # confined to the explicit sync command and never runs on app startup
        # or during a user request.
        from rapidocr import RapidOCR

        ocr = RapidOCR()
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
            result = ocr(pixmap.tobytes("png"))
            text = normalize_text("\n".join(result.txts or ()))
            if text:
                sections.append(
                    ExtractedSection(locator=f"第 {index + 1} 页（OCR）", text=text)
                )
            del result, pixmap
            gc.collect()
    document.close()
    if not sections:
        raise ValueError("PDF contains no extractable text after local OCR")
    return sections


def _source_bytes(source, state):
    source_id = source["source_id"]
    approved_hash = str(source.get("approved_sha256") or "").lower()
    if len(approved_hash) != 64:
        raise ValueError(f"{source_id} is missing an approved_sha256")
    if source["kind"] == "local_markdown":
        path = (BACKEND_DIR / source["path"]).resolve()
        if BACKEND_DIR.resolve() not in path.parents:
            raise ValueError("local source escaped backend directory")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != approved_hash:
            raise RuntimeError(f"{source_id} local content does not match its approved hash")
        return data, source.get("canonical_url", ""), False

    data = _download(source["url"])
    digest = hashlib.sha256(data).hexdigest()
    cache_dir = RUNTIME_DIR / "sources" / source_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    approved_path = cache_dir / "approved.pdf"
    if digest != approved_hash:
        quarantine = cache_dir / f"quarantine-{digest}.pdf"
        quarantine.write_bytes(data)
        if (
            not approved_path.exists()
            or hashlib.sha256(approved_path.read_bytes()).hexdigest() != approved_hash
        ):
            raise RuntimeError(f"{source_id} changed but approved snapshot is missing")
        return approved_path.read_bytes(), source["url"], True
    approved_path.write_bytes(data)
    state.setdefault("sources", {})[source_id] = {
        "approved_sha256": digest,
        "approved_at": int(time.time()),
        "url": source["url"],
    }
    return data, source["url"], False


def _build_chunks(manifest, state):
    all_chunks = []
    changed = []
    for source in manifest["sources"]:
        data, canonical_url, quarantined = _source_bytes(source, state)
        if quarantined:
            changed.append(source["source_id"])
        if source["kind"] == "local_markdown":
            sections = [
                ExtractedSection(locator="全文", text=data.decode("utf-8"))
            ]
        else:
            sections = _extract_pdf(data)
        for chunk in chunk_sections(
            source["source_id"], source["version"], sections
        ):
            all_chunks.append((source, canonical_url, chunk))
    return all_chunks, changed


def _qdrant_client():
    from qdrant_client import QdrantClient

    if Config.RAG_QDRANT_URL:
        return QdrantClient(
            url=Config.RAG_QDRANT_URL,
            api_key=Config.RAG_QDRANT_API_KEY or None,
            timeout=30,
        )
    Path(Config.RAG_STORAGE_PATH).mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=Config.RAG_STORAGE_PATH)


def _index(chunks, state):
    from qdrant_client import models

    fingerprint_payload = [
        {
            "source_id": source["source_id"],
            "version": source["version"],
            "audience": source["audience"],
            "category": source.get("category") or "general",
            "indicator_codes": source.get("indicator_codes") or [],
            "approved_sha256": source["approved_sha256"],
            "chunk_id": chunk.chunk_id,
        }
        for source, _url, chunk in chunks
    ]
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:12]
    collection = f"healthdoc_knowledge_{fingerprint}"
    client = _qdrant_client()
    if not client.collection_exists(collection):
        from fastembed import TextEmbedding

        Path(Config.RAG_MODEL_CACHE_PATH).mkdir(parents=True, exist_ok=True)
        try:
            embedder = TextEmbedding(
                model_name=Config.RAG_EMBEDDING_MODEL,
                cache_dir=Config.RAG_MODEL_CACHE_PATH,
                local_files_only=True,
            )
        except Exception:
            # First sync is the only path allowed to download the model.
            embedder = TextEmbedding(
                model_name=Config.RAG_EMBEDDING_MODEL,
                cache_dir=Config.RAG_MODEL_CACHE_PATH,
            )
        texts = [chunk.content for _source, _url, chunk in chunks]
        vectors = list(embedder.passage_embed(texts))
        client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(
                size=Config.RAG_VECTOR_SIZE, distance=models.Distance.COSINE
            ),
        )
        batch = []
        for (source, canonical_url, chunk), vector in zip(chunks, vectors):
            batch.append(
                models.PointStruct(
                    id=chunk.point_id,
                    vector=vector.tolist(),
                    payload={
                        "source_id": source["source_id"],
                        "chunk_id": chunk.chunk_id,
                        "title": source["title"],
                        "publisher": source["publisher"],
                        "canonical_url": canonical_url,
                        "version": source["version"],
                        "audience": source["audience"],
                        "category": source.get("category") or "general",
                        "indicator_codes": source.get("indicator_codes") or [],
                        "locator": chunk.locator,
                        "content": chunk.content,
                    },
                )
            )
            if len(batch) == 64:
                client.upsert(collection_name=collection, points=batch, wait=True)
                batch = []
        if batch:
            client.upsert(collection_name=collection, points=batch, wait=True)

    aliases = {item.alias_name: item.collection_name for item in client.get_aliases().aliases}
    old_collection = aliases.get(Config.RAG_COLLECTION_ALIAS)
    operations = []
    if old_collection and old_collection != collection:
        operations.append(
            models.DeleteAliasOperation(
                delete_alias=models.DeleteAlias(alias_name=Config.RAG_COLLECTION_ALIAS)
            )
        )
    if old_collection != collection:
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(
                    collection_name=collection, alias_name=Config.RAG_COLLECTION_ALIAS
                )
            )
        )
    if operations:
        client.update_collection_aliases(operations)
    state["active_collection"] = collection
    state["chunk_count"] = len(chunks)
    state["indexed_at"] = int(time.time())
    state["embedding_model"] = Config.RAG_EMBEDDING_MODEL
    client.close()
    return collection


def sync():
    manifest = _json(MANIFEST_PATH, {})
    if manifest.get("schema_version") != 1 or not manifest.get("sources"):
        raise ValueError("invalid or empty RAG source manifest")
    state = _json(STATE_PATH, {"schema_version": 1, "sources": {}})
    chunks, changed = _build_chunks(manifest, state)
    collection = _index(chunks, state)
    state["quarantined_sources"] = changed
    _write_json(STATE_PATH, state)
    print(
        json.dumps(
            {
                "status": "ok",
                "collection": collection,
                "sources": len(manifest["sources"]),
                "chunks": len(chunks),
                "quarantined_sources": changed,
            },
            ensure_ascii=False,
        )
    )


def approve_change(source_id: str, sha256: str):
    sha256 = sha256.lower()
    state = _json(STATE_PATH, {"schema_version": 1, "sources": {}})
    manifest = _json(MANIFEST_PATH, {})
    source = next(
        (item for item in manifest.get("sources", []) if item.get("source_id") == source_id),
        None,
    )
    if source is None:
        raise KeyError(f"unknown source_id: {source_id}")
    target = RUNTIME_DIR / "sources" / source_id / f"quarantine-{sha256}.pdf"
    if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
        raise FileNotFoundError("matching quarantined snapshot was not found")
    approved = target.with_name("approved.pdf")
    shutil.copy2(target, approved)
    source_state = state.setdefault("sources", {}).setdefault(source_id, {})
    source_state["approved_sha256"] = sha256
    source_state["approved_at"] = int(time.time())
    source["approved_sha256"] = sha256
    _write_json(MANIFEST_PATH, manifest)
    _write_json(STATE_PATH, state)
    print(json.dumps({"status": "approved", "source_id": source_id, "sha256": sha256}))


def main():
    parser = argparse.ArgumentParser(description="Synchronize the approved HealthDoc RAG corpus")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync")
    approve = sub.add_parser("approve-change")
    approve.add_argument("source_id")
    approve.add_argument("sha256")
    args = parser.parse_args()
    if args.command == "sync":
        sync()
    else:
        approve_change(args.source_id, args.sha256)


if __name__ == "__main__":
    main()
