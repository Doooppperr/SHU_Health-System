"""Measure authenticated RAG retrieval and model grounding citation quality."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time


BACKEND_DIR = Path(
    os.getenv("HEALTHDOC_BACKEND_DIR", Path(__file__).resolve().parents[1])
).resolve()
sys.path.insert(0, str(BACKEND_DIR))

from app import create_app
from app.ai.rag import (
    allowed_grounding_ids,
    format_knowledge_context,
    get_knowledge_retriever,
)
from app.ai.service import answer_authenticated_question, get_ai_client

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--max-cost-usd", type=float, default=5.0)
    parser.add_argument("--input-usd-per-million", type=float, default=3.0)
    parser.add_argument("--output-usd-per-million", type=float, default=5.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("grounding-quality-evaluation.json"),
    )
    return parser.parse_args()


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 4)


def main():
    args = parse_args()
    if not args.confirm_live:
        raise SystemExit("Refusing paid evaluation without --confirm-live")
    golden = json.loads(
        (BACKEND_DIR / "rag_sources" / "golden_queries.json").read_text(
            encoding="utf-8"
        )
    )["queries"]
    # Guest answers do not expose structured grounding IDs. Score the
    # authenticated contract only, including its negative/no-source cases.
    cases = [item for item in golden if item["audience"] == "authenticated"]

    app = create_app()
    results = []
    prompt_tokens = 0
    completion_tokens = 0
    with app.app_context():
        client = get_ai_client(app.config)
        retriever = get_knowledge_retriever(app)
        for item in cases:
            started = time.perf_counter()
            retrieval = retriever.retrieve(
                item["query"],
                audience="authenticated",
                indicator_codes=item.get("indicator_codes") or [],
                limit=5,
            )
            knowledge_context = format_knowledge_context(
                retrieval,
                max_chars=int(app.config.get("RAG_MAX_CONTEXT_CHARS", 12000)),
            )
            result = answer_authenticated_question(
                client,
                item["query"],
                [],
                "",
                "",
                knowledge_context,
                allowed_grounding_ids(retrieval),
            )
            usage = result.get("usage") or {}
            prompt_tokens += int(usage.get("prompt_tokens") or 0)
            completion_tokens += int(usage.get("completion_tokens") or 0)
            estimated_cost = (
                prompt_tokens * args.input_usd_per_million
                + completion_tokens * args.output_usd_per_million
            ) / 1_000_000
            if estimated_cost >= args.max_cost_usd:
                raise RuntimeError(
                    f"Hard cost ceiling reached: ${estimated_cost:.4f}"
                )
            cited_indexes = [
                int(value[1:]) - 1
                for value in result.get("grounding_source_ids") or []
                if value.startswith("K") and value[1:].isdigit()
            ]
            cited_source_ids = [
                retrieval.hits[index].source_id
                for index in cited_indexes
                if 0 <= index < len(retrieval.hits)
            ]
            retrieved_source_ids = [hit.source_id for hit in retrieval.hits]
            expected = item.get("expected_source_id")
            results.append(
                {
                    "query": item["query"],
                    "expected_source_id": expected,
                    "retrieved_source_ids": retrieved_source_ids,
                    "cited_source_ids": cited_source_ids,
                    "retrieval_correct": (
                        expected in retrieved_source_ids
                        if expected
                        else not retrieved_source_ids
                    ),
                    "citation_correct": (
                        expected in cited_source_ids
                        if expected
                        else not cited_source_ids
                    ),
                    "citation_count": len(cited_source_ids),
                    "latency_seconds": round(
                        time.perf_counter() - started,
                        4,
                    ),
                    "usage": usage,
                }
            )

    positive = [item for item in results if item["expected_source_id"]]
    negative = [item for item in results if not item["expected_source_id"]]
    cited_positive = [
        source_id
        for item in positive
        for source_id in item["cited_source_ids"]
    ]
    correct_citations = sum(
        source_id == item["expected_source_id"]
        for item in positive
        for source_id in item["cited_source_ids"]
    )
    estimated_cost = (
        prompt_tokens * args.input_usd_per_million
        + completion_tokens * args.output_usd_per_million
    ) / 1_000_000
    latencies = [item["latency_seconds"] for item in results]
    report = {
        "model": app.config.get("DEEPSEEK_MODEL"),
        "scenario_count": len(results),
        "answerable_scenarios": len(positive),
        "negative_scenarios": len(negative),
        "retrieval_top5_correct": sum(
            item["retrieval_correct"] for item in positive
        ),
        "retrieval_top5_recall": round(
            sum(item["retrieval_correct"] for item in positive)
            / len(positive),
            4,
        ),
        "citation_query_correct": sum(
            item["citation_correct"] for item in positive
        ),
        "citation_query_accuracy": round(
            sum(item["citation_correct"] for item in positive)
            / len(positive),
            4,
        ),
        "citation_precision": round(
            correct_citations / len(cited_positive),
            4,
        )
        if cited_positive
        else None,
        "citation_coverage": round(
            sum(bool(item["cited_source_ids"]) for item in positive)
            / len(positive),
            4,
        ),
        "negative_citation_abstentions": sum(
            item["citation_correct"] for item in negative
        ),
        "negative_citation_abstention_rate": round(
            sum(item["citation_correct"] for item in negative)
            / len(negative),
            4,
        )
        if negative
        else None,
        "latency_seconds": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": round(max(latencies), 4) if latencies else None,
        },
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": round(estimated_cost, 6),
        "estimated_cost_per_session_usd": round(
            estimated_cost / len(results),
            6,
        ),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "results"},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
