from __future__ import annotations

import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.ai.rag import QdrantKnowledgeRetriever  # noqa: E402
from app.config import Config  # noqa: E402


def main():
    path = BACKEND_DIR / "rag_sources" / "golden_queries.json"
    queries = json.loads(path.read_text(encoding="utf-8"))["queries"]
    retriever = QdrantKnowledgeRetriever(vars(Config))
    answerable = 0
    matched = 0
    no_answer = 0
    no_answer_no_match = 0
    failures = []
    try:
        for item in queries:
            result = retriever.retrieve(
                item["query"],
                audience=item["audience"],
                indicator_codes=item.get("indicator_codes") or [],
                limit=5,
            )
            source_ids = {hit.source_id for hit in result.hits}
            expected = item.get("expected_source_id")
            if expected:
                answerable += 1
                if expected in source_ids:
                    matched += 1
                else:
                    failures.append(
                        {"query": item["query"], "expected": expected, "actual": sorted(source_ids)}
                    )
            else:
                no_answer += 1
                if not result.hits:
                    no_answer_no_match += 1
    finally:
        retriever.close()
    recall = matched / answerable if answerable else 0
    payload = {
        "queries": len(queries),
        "answerable_queries": answerable,
        "top5_matches": matched,
        "top5_recall": round(recall, 4),
        "no_answer_queries": no_answer,
        "no_answer_no_match": no_answer_no_match,
        "failures": failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if recall < 0.9:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
