"""Run 200+ real-provider Agent routing scenarios with a hard cost ceiling."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import time
from datetime import date, timedelta
from pathlib import Path

import requests


TOOL_CASES = {
    "list_reports": [
        "列出我的体检报告",
        "我最近有哪些体检档案",
        "查看历年体检记录",
        "先找到我最新的健康报告",
        "我想看看过去的检查档案",
    ],
    "compute_indicator_trend": [
        "解释我的空腹血糖 FBG 变化趋势",
        "分析历次收缩压 SBP 有什么变化",
        "我的总胆固醇 TC 这几年升高了吗",
        "比较最近几次尿酸 UA 结果",
        "看看体重指数 BMI 的历史趋势",
    ],
    "search_institutions": [
        "帮我找体检机构",
        "有哪些可以预约的体检中心",
        "搜索附近的健康检查机构",
        "我想查看可用机构",
        "推荐几个系统内的体检分院",
    ],
    "compare_packages": [
        "比较套餐 1 和套餐 2",
        "帮我比较套餐 ID 1 和 2 的价格和项目",
        "套餐 1 与套餐 2 哪个更适合常规体检",
        "对比系统里的套餐 ID 1、2",
        "列出套餐 1 和 2 的差异",
    ],
    "check_availability": [
        "查询机构 1 明天一个人的预约余量",
        "机构 1 后天还有体检名额吗",
        "看看机构 1 三天后能不能预约",
        "检查机构 1 下周的空余名额",
        "机构 1 指定日期是否还能约两个人",
    ],
    "get_appointment_status": [
        "查询我的预约状态",
        "我有哪些体检预约",
        "查看最近预约是否成功",
        "列出我创建的预约组",
        "我的预约现在是什么状态",
    ],
    "create_support_handoff_draft": [
        "请创建人工客服工单处理账号问题",
        "我要转人工客服",
        "帮我提交一个客服工单",
        "这个问题需要人工处理",
        "请让客服人员联系我",
    ],
    "create_booking_draft": [
        "我想预约机构 1 的套餐 1，请先生成预约草稿",
        "帮我准备一个体检预约，提交前让我确认",
        "创建预约草稿但不要直接执行",
        "我准备预约体检，请收集需要的信息",
        "协助预约套餐，先给我确认卡片",
    ],
}


def scenarios():
    rows = []
    target_day = (date.today() + timedelta(days=14)).isoformat()
    for expected, templates in TOOL_CASES.items():
        for index in range(25):
            message = templates[index % len(templates)]
            if expected == "check_availability":
                message = f"查询机构 1 在 {target_day} 是否有 {1 + index % 2} 个预约名额"
            elif expected == "create_booking_draft":
                message = (
                    f"为我本人创建机构 1、套餐 1、{target_day} 的预约草稿；"
                    "身高 170 厘米、体重 65 千克，我已阅读预约须知，但提交前仍要让我确认"
                )
            rows.append(
                {
                    "id": f"{expected}-{index + 1:02d}",
                    "expected_tool": expected,
                    "message": message,
                }
            )
    return rows


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--max-cost-usd", type=float, default=10.0)
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Run only the first N scenarios for a smoke test; 0 runs all 200.",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--only-tool",
        choices=sorted(TOOL_CASES),
        help="Run one tool category during targeted tuning.",
    )
    parser.add_argument("--input-usd-per-million", type=float, default=3.0)
    parser.add_argument("--output-usd-per-million", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=Path("agent-evaluation.json"))
    return parser.parse_args()


def stream_events(response):
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


def main():
    args = parse_args()
    if not args.confirm_live:
        raise SystemExit("Refusing paid evaluation without --confirm-live")
    token = os.getenv("HEALTHDOC_EVAL_ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit("Set HEALTHDOC_EVAL_ACCESS_TOKEN for a dedicated evaluation user")
    headers = {"Authorization": f"Bearer {token}"}
    capabilities = requests.get(
        f"{args.base_url.rstrip('/')}/api/agent/capabilities", timeout=10
    ).json()
    if capabilities.get("provider_mode") != "live":
        raise SystemExit("Target Agent is not using the live provider")

    results = []
    prompt_tokens = 0
    completion_tokens = 0
    started = time.time()
    cases = scenarios()
    if args.only_tool:
        cases = [item for item in cases if item["expected_tool"] == args.only_tool]
    if args.limit:
        if args.limit < 1:
            raise SystemExit("--limit must be positive")
        cases = cases[: args.limit]
    def run_case_once(case):
        session = requests.Session()
        session.headers.update(headers)
        thread = session.post(
            f"{args.base_url.rstrip('/')}/api/agent/threads", timeout=10
        )
        thread.raise_for_status()
        thread_id = thread.json()["item"]["id"]
        response = session.post(
            f"{args.base_url.rstrip('/')}/api/agent/threads/{thread_id}/runs/stream",
            json={"message": case["message"]},
            headers={"Accept": "text/event-stream"},
            stream=True,
            timeout=(10, 190),
        )
        response.raise_for_status()
        tools = []
        completed_tools = {}
        error = None
        usage = {}
        for event, data in stream_events(response):
            if event == "tool_started":
                tools.append(data.get("name"))
            elif event == "tool_completed":
                completed_tools[data.get("name")] = data.get("ok") is True
            elif event == "done":
                usage = data.get("usage") or {}
            elif event == "error":
                error = data.get("code")
        return {
            **case,
            "actual_tools": tools,
            "completed_tools": completed_tools,
            "passed": completed_tools.get(case["expected_tool"]) is True,
            "error": error,
            "usage": usage,
        }

    def run_case(case):
        for attempt in range(3):
            try:
                return run_case_once(case)
            except requests.RequestException as exc:
                if attempt == 2:
                    return {
                        **case,
                        "actual_tools": [],
                        "completed_tools": {},
                        "passed": False,
                        "error": f"transport_error:{type(exc).__name__}",
                        "usage": {},
                    }
                time.sleep(1 + attempt)

    workers = max(1, min(args.workers, 8))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(run_case, case): case for case in cases}
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
            if cost >= args.max_cost_usd:
                for pending in futures:
                    pending.cancel()
                raise RuntimeError(f"Hard cost ceiling reached: ${cost:.4f}")
    results.sort(key=lambda item: item["id"])

    passed = sum(item["passed"] for item in results)
    report = {
        "model": capabilities.get("model"),
        "scenario_count": len(results),
        "passed": passed,
        "tool_selection_accuracy": passed / len(results),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_usd": (
            prompt_tokens * args.input_usd_per_million
            + completion_tokens * args.output_usd_per_million
        ) / 1_000_000,
        "duration_seconds": round(time.time() - started, 2),
        "results": results,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, ensure_ascii=False))
    if len(results) >= 200 and report["tool_selection_accuracy"] < 0.95:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
