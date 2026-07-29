from __future__ import annotations

import os

import httpx
from mcp.server import MCPServer
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
from mcp.server.transport_security import TransportSecuritySettings


BACKEND_URL = os.getenv("HEALTHDOC_INTERNAL_URL", "http://127.0.0.1:5050").rstrip("/")
INTERNAL_KEY = os.getenv("MCP_INTERNAL_KEY", "")
ISSUER = os.getenv("OAUTH_ISSUER", "https://127.0.0.1")
RESOURCE = os.getenv("MCP_RESOURCE_URL", f"{ISSUER.rstrip('/')}/mcp")


def _bearer(ctx: Context) -> str:
    value = str(ctx.headers.get("authorization") or "")
    if not value.lower().startswith("bearer "):
        raise PermissionError("Bearer token required")
    return value.split(" ", 1)[1].strip()


async def _post(path, payload):
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{BACKEND_URL}{path}",
            headers={"X-HealthDoc-Internal-Key": INTERNAL_KEY},
            json=payload,
        )
    response.raise_for_status()
    return response.json()


class HealthDocTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str):
        try:
            data = await _post("/api/internal/mcp/verify", {"token": token})
        except Exception:
            return None
        if not data.get("active"):
            return None
        return AccessToken(
            token=token,
            client_id=data["client_id"],
            scopes=data["scopes"],
            expires_at=data["expires_at"],
            resource=data["resource"],
            subject=str(data["user_id"]),
        )


mcp = MCPServer(
    name="HealthDoc",
    title="HealthDoc Agent Tools",
    description="受 OAuth 和逐次确认保护的健康档案、机构、套餐与预约工具。",
    version="1.0.0",
    token_verifier=HealthDocTokenVerifier(),
    auth=AuthSettings(
        issuer_url=ISSUER,
        resource_server_url=RESOURCE,
        required_scopes=[],
    ),
)


async def _call(ctx, name, arguments):
    result = await _post(
        "/api/internal/mcp/tool",
        {"token": _bearer(ctx), "name": name, "arguments": arguments},
    )
    return result["result"]


@mcp.tool(description="列出本人或已授权亲友的体检报告。")
async def list_reports(ctx: Context, owner_id: int | None = None, limit: int = 10):
    return await _call(ctx, "list_reports", {"owner_id": owner_id, "limit": limit})


@mcp.tool(description="读取授权报告的结构化指标事实。")
async def get_report_facts(ctx: Context, report_ids: list[int], indicator_codes: list[str] | None = None):
    return await _call(ctx, "get_report_facts", {"report_ids": report_ids, "indicator_codes": indicator_codes or []})


@mcp.tool(description="确定性计算一个指标的历次变化。")
async def compute_indicator_trend(ctx: Context, indicator_code: str, owner_id: int | None = None, limit: int = 10):
    return await _call(ctx, "compute_indicator_trend", {"owner_id": owner_id, "indicator_code": indicator_code, "limit": limit})


@mcp.tool(description="搜索体检机构。")
async def search_institutions(ctx: Context, keyword: str = "", district: str | None = None, limit: int = 8):
    return await _call(ctx, "search_institutions", {"keyword": keyword, "district": district, "limit": limit})


@mcp.tool(description="比较指定体检套餐。")
async def compare_packages(ctx: Context, package_ids: list[int]):
    return await _call(ctx, "compare_packages", {"package_ids": package_ids})


@mcp.tool(description="查询机构指定日期的预约余量。")
async def check_availability(ctx: Context, institution_id: int, appointment_date: str, party_size: int = 1):
    return await _call(ctx, "check_availability", {"institution_id": institution_id, "appointment_date": appointment_date, "party_size": party_size})


@mcp.tool(description="查询当前用户创建的预约组。")
async def get_appointment_status(ctx: Context, group_id: int | None = None, limit: int = 10):
    return await _call(ctx, "get_appointment_status", {"group_id": group_id, "limit": limit})


@mcp.tool(description="创建预约草稿；用户必须在 HealthDoc 页面确认。")
async def create_booking_draft(ctx: Context, institution_id: int, package_id: int, appointment_date: str, participant_user_ids: list[int], participant_intakes: list[dict], notice_confirmed: bool):
    return await _call(
        ctx,
        "create_booking_draft",
        {
            "institution_id": institution_id,
            "package_id": package_id,
            "appointment_date": appointment_date,
            "participant_user_ids": participant_user_ids,
            "participant_intakes": participant_intakes,
            "notice_confirmed": notice_confirmed,
        },
    )


@mcp.tool(description="创建整组取消草稿；用户必须在 HealthDoc 页面确认。")
async def create_cancellation_draft(ctx: Context, group_id: int):
    return await _call(ctx, "create_cancellation_draft", {"group_id": group_id})


@mcp.tool(description="创建空位候补草稿；用户必须在 HealthDoc 页面确认。")
async def create_waitlist_draft(ctx: Context, institution_id: int, package_id: int, appointment_date: str, participant_user_ids: list[int] | None = None):
    return await _call(ctx, "create_waitlist_draft", {"institution_id": institution_id, "package_id": package_id, "appointment_date": appointment_date, "participant_user_ids": participant_user_ids or []})


@mcp.tool(description="创建人工客服工单草稿；用户必须在 HealthDoc 页面确认。")
async def create_support_handoff_draft(ctx: Context, category: str, summary: str, priority: str = "normal"):
    return await _call(ctx, "create_support_handoff_draft", {"category": category, "summary": summary, "priority": priority})


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.getenv("MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("MCP_PORT", "5051")),
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[value for value in os.getenv("MCP_ALLOWED_HOSTS", "127.0.0.1").split(",") if value],
            allowed_origins=[value for value in os.getenv("MCP_ALLOWED_ORIGINS", ISSUER).split(",") if value],
        ),
    )
