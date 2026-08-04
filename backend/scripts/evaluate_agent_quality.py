"""Evaluate real-provider Agent quality with semantic checks and latency percentiles.

The existing 200-case evaluator measures whether the expected tool eventually
completed. This companion evaluator preserves that metric and adds:

- semantic parameter/output checks for the 100 cases with deterministic values;
- safe end-to-end completion (read result or confirmation draft);
- per-request latency and time-to-first-text percentiles;
- recovered tool failure counts;
- automatic rejection of drafts and thread cleanup.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
import os
from pathlib import Path
import re
import sys
import time

import requests

from evaluate_agent import TOOL_CASES, scenarios


CASE_PREFIXES = [
    "",
    "请直接处理：",
    "麻烦你帮我，",
    "我想确认一下，",
    "请使用系统里的信息，",
]
CASE_SUFFIXES = [
    "",
    "，请给出清楚的结果。",
    "，不要跳过必要的查询。",
    "，请按当前账号权限处理。",
    "，结果用简洁中文说明。",
]
TREND_BODIES = [
    "分析我的 {code} 历次变化趋势",
    "解释一下我过去几次 {code} 指标的变化",
    "查看我的 {code} 历史数据是升高还是降低",
    "比较我各次体检中的 {code} 结果",
    "请计算并说明我的 {code} 指标趋势",
]
AVAILABILITY_BODIES = [
    "查询机构 1 在 {day} 是否有 {party} 个预约名额",
    "请检查机构 1 在 {day} 还能不能预约 {party} 个人，需要 {party} 个名额",
    "机构 1 在 {day} 的余量够不够 {party} 个人，也就是 {party} 个名额",
    "想知道机构 1 在 {day} 是否可以安排 {party} 人，需要 {party} 个预约位",
    "帮我核实机构 1 在 {day} 有没有 {party} 个可预约名额",
]
BOOKING_BODIES = [
    (
        "为我本人创建机构 1、套餐 1、{day} 的预约草稿；"
        "身高 170 厘米、体重 65 千克，我已阅读预约须知，提交前让我确认"
    ),
    (
        "预约日期是 {day}，本人去机构 1 做套餐 1；使用身高 170 厘米、"
        "体重 65 千克，我同意预约须知，只生成待确认草稿"
    ),
    (
        "请准备本人在 {day} 的体检预约：机构 1、套餐 1、身高 170 厘米、"
        "体重 65 千克，预约须知已确认，不要直接提交"
    ),
    (
        "我要本人预约机构 1 的套餐 1，日期 {day}，身高 170 厘米、"
        "体重 65 千克，已阅读并同意预约须知，请先生成确认卡片"
    ),
    (
        "{day} 帮我本人约机构 1 的套餐 1；资料为 170 厘米和 65 千克，"
        "预约须知已经同意，必须等我确认后才能执行"
    ),
]


def expanded_scenarios(target_count: int) -> list[dict]:
    if target_count <= 200:
        return scenarios()[:target_count]
    if target_count % len(TOOL_CASES) != 0:
        raise ValueError(
            f"target_count must be divisible by {len(TOOL_CASES)}"
        )
    per_tool = target_count // len(TOOL_CASES)
    if per_tool > 125:
        raise ValueError("target_count currently supports at most 1000 cases")

    from datetime import date, timedelta

    rows = []
    target_day = (date.today() + timedelta(days=14)).isoformat()
    trend_codes = ["FBG", "SBP", "TC", "UA", "BMI"]
    for expected, templates in TOOL_CASES.items():
        for index in range(per_tool):
            body_index = index % 5
            prefix = CASE_PREFIXES[(index // 5) % 5]
            suffix = CASE_SUFFIXES[(index // 25) % 5]
            if expected == "compute_indicator_trend":
                body = TREND_BODIES[body_index].format(
                    code=trend_codes[(index // 5) % 5]
                )
            elif expected == "check_availability":
                body = AVAILABILITY_BODIES[body_index].format(
                    day=target_day,
                    party=1 + ((index // 5) % 2),
                )
            elif expected == "create_booking_draft":
                body = BOOKING_BODIES[body_index].format(day=target_day)
            else:
                body = templates[body_index]
            rows.append(
                {
                    "id": f"{expected}-{index + 1:03d}",
                    "expected_tool": expected,
                    "message": f"{prefix}{body}{suffix}",
                }
            )
    return rows


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--target-count",
        type=int,
        default=200,
        help="Generate a balanced suite of 200 to 1000 scenarios.",
    )
    parser.add_argument(
        "--server-user",
        default="",
        help="Generate a short-lived token for this username in a trusted server shell.",
    )
    parser.add_argument("--max-cost-usd", type=float, default=5.0)
    parser.add_argument("--input-usd-per-million", type=float, default=3.0)
    parser.add_argument("--output-usd-per-million", type=float, default=5.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("agent-quality-evaluation.json"),
    )
    return parser.parse_args()


def stream_events(response):
    response.raise_for_status()
    response.encoding = "utf-8"
    current = "message"
    data = []
    for raw in response.iter_lines(decode_unicode=True):
        line = raw or ""
        if line.startswith("event:"):
            current = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line and data:
            yield current, json.loads("\n".join(data))
            current, data = "message", []


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return round(ordered[index], 4)


def _last_evidence(evidence, tool_name):
    rows = evidence.get(tool_name) or []
    return rows[-1] if rows else None


def semantic_parameters_correct(case, evidence, approval, tool_failures):
    """Return True/False for deterministic cases and None for unscored cases."""
    expected = case["expected_tool"]
    if expected not in {
        "compute_indicator_trend",
        "compare_packages",
        "check_availability",
        "create_booking_draft",
    }:
        return None
    if expected in tool_failures:
        return False

    if expected == "compute_indicator_trend":
        result = _last_evidence(evidence, expected) or {}
        match = re.search(r"\b(FBG|SBP|TC|UA|BMI)\b", case["message"], re.I)
        actual = (result.get("indicator") or {}).get("code")
        return bool(match and actual and actual.upper() == match.group(1).upper())

    if expected == "compare_packages":
        result = _last_evidence(evidence, expected) or {}
        actual_ids = {item.get("id") for item in result.get("packages") or []}
        return {1, 2}.issubset(actual_ids)

    if expected == "check_availability":
        result = _last_evidence(evidence, expected) or {}
        match = re.search(
            r"机构\s*(\d+)\s*在\s*(\d{4}-\d{2}-\d{2}).*?(\d+)\s*个",
            case["message"],
        )
        if not match:
            return False
        return (
            result.get("institution_id") == int(match.group(1))
            and result.get("appointment_date") == match.group(2)
            and result.get("party_size") == int(match.group(3))
        )

    summary = (approval or {}).get("summary") or {}
    date_match = re.search(r"\d{4}-\d{2}-\d{2}", case["message"])
    return (
        (approval or {}).get("action_type") == "booking"
        and date_match is not None
        and summary.get("体检日期") == date_match.group(0)
        and summary.get("预约人数") == 1
        and summary.get("身高/体重") == "170 cm / 65 kg"
    )


def run_case_once(case, base_url, headers):
    session = requests.Session()
    session.headers.update(headers)
    thread_id = None
    action_id = None
    cleanup = {"action_rejected": None, "thread_cleared": False}
    try:
        thread = session.post(f"{base_url}/api/agent/threads", timeout=10)
        thread.raise_for_status()
        thread_id = thread.json()["item"]["id"]

        started = time.perf_counter()
        response = session.post(
            f"{base_url}/api/agent/threads/{thread_id}/runs/stream",
            json={"message": case["message"]},
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=(10, 190),
        )
        tools = []
        completed = {}
        tool_failures = set()
        evidence = {}
        approval = None
        error = None
        usage = {}
        answer_parts = []
        first_text_seconds = None
        done_received = False
        for event, data in stream_events(response):
            if event == "tool_started":
                tools.append(data.get("name"))
            elif event == "tool_completed":
                name = data.get("name")
                ok = data.get("ok") is True
                completed[name] = ok
                if not ok:
                    tool_failures.add(name)
            elif event == "evidence":
                evidence.setdefault(data.get("tool"), []).append(
                    data.get("result") or {}
                )
            elif event == "approval_required":
                approval = data
                action_id = data.get("action_id")
            elif event == "delta":
                if first_text_seconds is None:
                    first_text_seconds = time.perf_counter() - started
                answer_parts.append(data.get("content") or "")
            elif event == "done":
                usage = data.get("usage") or {}
                done_received = True
            elif event == "error":
                error = data.get("code")
        latency_seconds = time.perf_counter() - started

        expected = case["expected_tool"]
        selection_ok = completed.get(expected) is True
        parameter_ok = semantic_parameters_correct(
            case,
            evidence,
            approval,
            tool_failures,
        )
        if expected.endswith("_draft"):
            expected_action = (
                "support_handoff"
                if expected == "create_support_handoff_draft"
                else "booking"
            )
            outcome_ok = (
                approval is not None
                and approval.get("action_type") == expected_action
            )
        else:
            outcome_ok = bool(_last_evidence(evidence, expected))
        task_completed = (
            selection_ok
            and outcome_ok
            and error is None
            and done_received
            and bool("".join(answer_parts).strip())
        )
        return {
            **case,
            "actual_tools": tools,
            "completed_tools": completed,
            "tool_failures": sorted(tool_failures),
            "tool_selection_correct": selection_ok,
            "semantic_parameter_correct": parameter_ok,
            "task_completed": task_completed,
            "error": error,
            "usage": usage,
            "latency_seconds": round(latency_seconds, 4),
            "time_to_first_text_seconds": (
                round(first_text_seconds, 4)
                if first_text_seconds is not None
                else None
            ),
            "_cleanup": cleanup,
        }
    finally:
        if action_id:
            try:
                decision = session.post(
                    f"{base_url}/api/agent/actions/{action_id}/decision/stream",
                    json={"decision": "reject"},
                    headers={"Accept": "text/event-stream"},
                    timeout=(10, 30),
                )
                cleanup["action_rejected"] = decision.ok
            except requests.RequestException:
                cleanup["action_rejected"] = False
        if thread_id:
            try:
                cleared = session.delete(
                    f"{base_url}/api/agent/threads/{thread_id}",
                    timeout=10,
                )
                cleanup["thread_cleared"] = cleared.status_code == 204
            except requests.RequestException:
                cleanup["thread_cleared"] = False


def run_case(case, base_url, headers):
    for attempt in range(3):
        try:
            return run_case_once(case, base_url, headers)
        except requests.RequestException as exc:
            if attempt == 2:
                return {
                    **case,
                    "actual_tools": [],
                    "completed_tools": {},
                    "tool_failures": [],
                    "tool_selection_correct": False,
                    "semantic_parameter_correct": False,
                    "task_completed": False,
                    "error": f"transport_error:{type(exc).__name__}",
                    "usage": {},
                    "latency_seconds": None,
                    "time_to_first_text_seconds": None,
                    "_cleanup": {
                        "action_rejected": None,
                        "thread_cleared": False,
                    },
                }
            time.sleep(1 + attempt)


def main():
    args = parse_args()
    if not args.confirm_live:
        raise SystemExit("Refusing paid evaluation without --confirm-live")
    token = os.getenv("HEALTHDOC_EVAL_ACCESS_TOKEN", "").strip()
    if not token and args.server_user:
        backend_dir = Path(
            os.getenv("HEALTHDOC_BACKEND_DIR", Path.cwd())
        ).resolve()
        sys.path.insert(0, str(backend_dir))
        from flask_jwt_extended import create_access_token

        from app import create_app
        from app.models import User

        app = create_app()
        with app.app_context():
            user = User.query.filter_by(
                username=args.server_user,
                role="user",
            ).one()
            token = create_access_token(
                identity=str(user.id),
                additional_claims={
                    "role": user.role,
                    "token_version": user.token_version,
                },
            )
    if not token:
        raise SystemExit(
            "Set HEALTHDOC_EVAL_ACCESS_TOKEN or use --server-user in a trusted shell"
        )
    base_url = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    capabilities = requests.get(
        f"{base_url}/api/agent/capabilities",
        timeout=10,
    ).json()
    if capabilities.get("provider_mode") != "live":
        raise SystemExit("Target Agent is not using the live provider")

    cases = expanded_scenarios(args.target_count)
    if args.limit:
        cases = cases[: args.limit]
    workers = max(1, min(args.workers, 8))
    results = []
    prompt_tokens = 0
    completion_tokens = 0
    started = time.perf_counter()
    next_progress = 50

    for offset in range(0, len(cases), workers):
        batch = cases[offset : offset + workers]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(run_case, case, base_url, headers): case
                for case in batch
            }
            for future in as_completed(futures):
                item = future.result()
                results.append(item)
                usage = item["usage"]
                prompt_tokens += int(usage.get("prompt_tokens") or 0)
                completion_tokens += int(usage.get("completion_tokens") or 0)
        cost = (
            prompt_tokens * args.input_usd_per_million
            + completion_tokens * args.output_usd_per_million
        ) / 1_000_000
        if len(results) >= next_progress or len(results) == len(cases):
            print(
                json.dumps(
                    {
                        "progress": len(results),
                        "total": len(cases),
                        "estimated_cost_usd": round(cost, 4),
                        "elapsed_seconds": round(time.perf_counter() - started, 1),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            while next_progress <= len(results):
                next_progress += 50
        if cost >= args.max_cost_usd:
            raise RuntimeError(f"Hard cost ceiling reached: ${cost:.4f}")

    results.sort(key=lambda item: item["id"])
    parameter_rows = [
        item
        for item in results
        if item["semantic_parameter_correct"] is not None
    ]
    latencies = [
        item["latency_seconds"]
        for item in results
        if item["latency_seconds"] is not None
    ]
    first_text = [
        item["time_to_first_text_seconds"]
        for item in results
        if item["time_to_first_text_seconds"] is not None
    ]
    estimated_cost = (
        prompt_tokens * args.input_usd_per_million
        + completion_tokens * args.output_usd_per_million
    ) / 1_000_000
    report = {
        "model": capabilities.get("model"),
        "scenario_count": len(results),
        "tool_selection_correct": sum(
            item["tool_selection_correct"] for item in results
        ),
        "tool_selection_accuracy": round(
            sum(item["tool_selection_correct"] for item in results)
            / len(results),
            4,
        ),
        "semantic_parameter_scenarios": len(parameter_rows),
        "semantic_parameter_correct": sum(
            item["semantic_parameter_correct"] for item in parameter_rows
        ),
        "semantic_parameter_accuracy": (
            round(
                sum(
                    item["semantic_parameter_correct"]
                    for item in parameter_rows
                )
                / len(parameter_rows),
                4,
            )
            if parameter_rows
            else None
        ),
        "tasks_completed": sum(item["task_completed"] for item in results),
        "task_completion_rate": round(
            sum(item["task_completed"] for item in results) / len(results),
            4,
        ),
        "cases_with_recovered_tool_failures": sum(
            bool(item["tool_failures"]) for item in results
        ),
        "latency_seconds": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "max": round(max(latencies), 4) if latencies else None,
        },
        "time_to_first_text_seconds": {
            "p50": percentile(first_text, 0.50),
            "p95": percentile(first_text, 0.95),
            "max": round(max(first_text), 4) if first_text else None,
        },
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": round(estimated_cost, 6),
        "estimated_cost_per_session_usd": round(
            estimated_cost / len(results),
            6,
        ),
        "wall_clock_seconds": round(time.perf_counter() - started, 2),
        "cleanup_failures": sum(
            not item["_cleanup"]["thread_cleared"] for item in results
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
    if len(results) >= 200 and (
        report["tool_selection_accuracy"] < 0.95
        or report["task_completion_rate"] < 0.95
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
