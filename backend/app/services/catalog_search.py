from __future__ import annotations

import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from flask import current_app
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import selectinload

from app.ai.service import AiConfigurationError, AiProviderError, get_ai_client
from app.models import Institution, Organization, Package, PackageVersion, PackageVersionDomain
from app.services.ai_rate_limit import ai_rate_limited


_ALIAS_GROUPS = (
    ("女性", ("女性", "女生", "女士", "妇女", "妇科", "乳腺", "妈妈", "母亲"), ("女性", "女生", "妇科", "乳腺", "female")),
    ("男性", ("男性", "男生", "男士", "男科", "爸爸", "父亲"), ("男性", "男生", "男士", "male")),
    ("长辈", ("长辈", "老人", "老年", "父母", "爸妈", "中老年"), ("长辈", "老人", "老年", "50 岁以上")),
    ("职场", ("职场", "上班", "白领", "工作人群", "商务人士"), ("职场", "工作", "白领", "商务")),
    ("家庭", ("家庭", "家人", "同行", "照护者"), ("家庭", "家人", "同行", "照护")),
    ("心血管", ("心血管", "心脑血管", "心脏", "心电", "血压", "循环"), ("心血管", "心脑血管", "心电", "循环")),
    ("代谢", ("代谢", "血糖", "血脂", "糖尿病", "体重", "甲状腺"), ("代谢", "血糖", "血脂", "体重", "甲状腺")),
    ("呼吸", ("呼吸", "肺", "肺功能", "吸烟", "咳嗽"), ("呼吸", "肺功能", "肺部")),
    ("消化", ("消化", "肝胆", "肝脏", "脂肪肝", "胃肠"), ("消化", "肝胆", "肝脏", "胃肠")),
    ("基础", ("基础", "常规", "年度", "综合", "全身"), ("基础", "常规", "年度", "综合")),
    ("交通", ("地铁", "交通", "附近", "靠近", "方便到达"), ("地铁", "交通", "路线")),
)
_DISTRICTS = (
    "黄浦", "徐汇", "长宁", "静安", "普陀", "虹口", "杨浦", "浦东",
    "闵行", "宝山", "嘉定", "金山", "松江", "青浦", "奉贤", "崇明",
)
_STOP_WORDS = (
    "体检中心", "体检机构", "体检套餐", "有没有", "可以", "希望", "适合",
    "想要", "想找", "帮我", "给我", "一个", "一家", "比较", "推荐", "套餐",
    "机构", "体检", "检查", "靠近", "附近", "偏向", "主要", "相关", "的",
)
_COMPLEX_MARKERS = ("想", "找", "适合", "推荐", "预算", "附近", "靠近", "给", "希望", "同时", "最好")
_PRICE_PATTERN = re.compile(r"(?:预算|不超过|低于|以内|最多)\s*[¥￥]?\s*(\d{2,6}(?:\.\d{1,2})?)")


class CatalogIntent(BaseModel):
    intent_summary: str = Field(default="", max_length=80)
    audience_terms: list[str] = Field(default_factory=list, max_length=8)
    health_topics: list[str] = Field(default_factory=list, max_length=8)
    location_terms: list[str] = Field(default_factory=list, max_length=8)
    package_terms: list[str] = Field(default_factory=list, max_length=8)
    budget_max: float | None = Field(default=None, ge=0, le=1_000_000)


@dataclass(frozen=True)
class SearchTerm:
    value: str
    direct: bool


_intent_cache: OrderedDict[str, tuple[float, CatalogIntent]] = OrderedDict()
_intent_cache_lock = threading.Lock()


def normalize_search_mode(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"content", "hybrid"} else None


def _fold(value) -> str:
    return "".join(str(value or "").casefold().split())


def _unique(values):
    result = []
    seen = set()
    for value in values:
        normalized = str(value or "").strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def _content_intent(query: str):
    compact = _fold(query)
    terms = [SearchTerm(query, True)]
    labels = []
    group_count = 0
    for label, aliases, expansions in _ALIAS_GROUPS:
        matches = [alias for alias in aliases if _fold(alias) in compact]
        if not matches:
            continue
        group_count += 1
        labels.append(label)
        terms.extend(SearchTerm(item, True) for item in matches)
        terms.extend(SearchTerm(item, False) for item in expansions)
    for district in _DISTRICTS:
        if district in query:
            labels.append(f"{district}区")
            terms.extend((SearchTerm(district, True), SearchTerm(f"{district}区", False)))
    cleaned = query
    for word in sorted(_STOP_WORDS, key=len, reverse=True):
        cleaned = cleaned.replace(word, " ")
    for token in re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]{2,}", cleaned):
        terms.append(SearchTerm(token, True))
    deduped = []
    seen = set()
    for term in terms:
        key = (_fold(term.value), term.direct)
        if key[0] and key not in seen:
            seen.add(key)
            deduped.append(term)
    price = _PRICE_PATTERN.search(query)
    budget_max = float(price.group(1)) if price else None
    if budget_max is not None:
        labels.append(f"预算 ¥{budget_max:g} 以内")
    complex_query = group_count >= 2 or (
        len(compact) >= 8 and any(marker in query for marker in _COMPLEX_MARKERS)
    )
    return deduped, budget_max, _unique(labels), complex_query


def _field_score(value, terms, *, exact: int, contains: int, inferred: int):
    folded = _fold(value)
    if not folded:
        return 0
    best = 0
    for term in terms:
        needle = _fold(term.value)
        if not needle:
            continue
        if folded == needle:
            best = max(best, exact if term.direct else inferred + 10)
        elif needle in folded:
            best = max(best, contains if term.direct else inferred)
    return best


def _current_version(package):
    return next((row for row in package.versions if row.id == package.current_version_id), None)


def _active_packages(branch):
    return [
        package for package in branch.packages
        if package.is_active and package.current_version_id is not None and _current_version(package)
    ]


def _package_match(package, terms, budget_max):
    version = _current_version(package)
    domain_text = " ".join(
        f"{link.domain.code} {link.domain.name} {link.domain.description or ''}"
        for link in (version.domains if version else [])
        if link.domain and link.domain.is_active
    )
    gender_text = {"female": "女性 女士", "male": "男性 男士", "all": "不限人群"}.get(package.gender_scope, package.gender_scope)
    fields = (
        (package.name, 105, 90, 72, "套餐名称"),
        (package.focus_area, 90, 80, 68, "关注方向"),
        (package.audience, 88, 78, 66, "适用人群"),
        (gender_text, 92, 82, 70, "适用人群"),
        (domain_text, 84, 74, 64, "健康领域"),
        (package.description, 58, 46, 34, "套餐说明"),
    )
    scored = []
    for value, exact, contains, inferred, reason in fields:
        score = _field_score(value, terms, exact=exact, contains=contains, inferred=inferred)
        if score:
            scored.append((score, reason))
    if budget_max is not None:
        if float(package.price) > budget_max:
            return None
        scored.append((52, f"价格不超过 ¥{budget_max:g}"))
    if not scored:
        return None
    scored.sort(reverse=True)
    score = min(scored[0][0] + min(sum(item[0] for item in scored[1:]) // 8, 18), 125)
    reasons = _unique(item[1] for item in scored)
    return {
        "package": package,
        "score": score,
        "reasons": reasons,
        "public": {
            "id": package.id,
            "name": package.name,
            "focus_area": package.focus_area,
            "audience": package.audience,
            "gender_scope": package.gender_scope,
            "price": float(package.price),
            "reason": "、".join(reasons[:2]),
        },
    }


def _evaluate(organizations, terms, budget_max):
    matches = []
    for organization in organizations:
        org_fields = (
            (organization.name, 120, 100, 78, "机构名称"),
            (organization.service_features or [], 82, 68, 54, "机构服务特点"),
            (organization.description, 52, 40, 30, "机构介绍"),
        )
        org_scored = []
        for value, exact, contains, inferred, reason in org_fields:
            text = " ".join(value) if isinstance(value, list) else value
            score = _field_score(text, terms, exact=exact, contains=contains, inferred=inferred)
            if score:
                org_scored.append((score, reason))
        org_score = max((item[0] for item in org_scored), default=0)
        branch_matches = []
        for branch in organization.branches:
            if not branch.is_active or branch.operations_suspended_at is not None:
                continue
            branch_fields = (
                (branch.branch_name, 112, 96, 74, "分院名称"),
                (branch.district, 104, 90, 70, "所在地区"),
                (branch.address, 90, 76, 62, "详细地址"),
                (branch.metro_info, 88, 75, 64, "交通信息"),
                (branch.description, 52, 40, 30, "分院介绍"),
            )
            branch_scored = []
            for value, exact, contains, inferred, reason in branch_fields:
                score = _field_score(value, terms, exact=exact, contains=contains, inferred=inferred)
                if score:
                    branch_scored.append((score, reason))
            package_matches = []
            for package in _active_packages(branch):
                package_match = _package_match(package, terms, budget_max)
                if package_match:
                    package_matches.append(package_match)
            package_matches.sort(key=lambda item: (-item["score"], item["package"].id))
            branch_score = max(
                [item[0] for item in branch_scored] + [item["score"] for item in package_matches] + ([org_score - 1] if org_score else []),
                default=0,
            )
            if branch_score:
                reasons = _unique(
                    [item[1] for item in sorted(branch_scored, reverse=True)]
                    + [f"匹配套餐：{item['package'].name}" for item in package_matches[:2]]
                    + (["机构主体匹配"] if org_score and not branch_scored and not package_matches else [])
                )
                branch_matches.append({
                    "branch": branch,
                    "score": branch_score,
                    "reasons": reasons,
                    "matched_packages": package_matches,
                })
        if branch_matches:
            branch_matches.sort(key=lambda item: (-item["score"], item["branch"].id))
            total_score = max(org_score, branch_matches[0]["score"])
            if org_score and branch_matches[0]["score"] != org_score:
                total_score = min(total_score + 8, 130)
            matches.append({
                "organization": organization,
                "score": total_score,
                "reasons": _unique([item[1] for item in sorted(org_scored, reverse=True)] + branch_matches[0]["reasons"]),
                "branches": branch_matches,
            })
    matches.sort(key=lambda item: (-item["score"], item["organization"].id))
    return matches


def _cache_get(query):
    ttl = max(int(current_app.config.get("CATALOG_AI_CACHE_TTL_SECONDS", 600)), 0)
    now = time.monotonic()
    with _intent_cache_lock:
        cached = _intent_cache.get(query)
        if cached is None:
            return None
        created_at, intent = cached
        if now - created_at > ttl:
            _intent_cache.pop(query, None)
            return None
        _intent_cache.move_to_end(query)
        return intent


def _cache_put(query, intent):
    max_size = max(int(current_app.config.get("CATALOG_AI_CACHE_SIZE", 256)), 1)
    with _intent_cache_lock:
        _intent_cache[query] = (time.monotonic(), intent)
        _intent_cache.move_to_end(query)
        while len(_intent_cache) > max_size:
            _intent_cache.popitem(last=False)


def _interpret_with_model(query: str, user=None):
    cached = _cache_get(query)
    if cached is not None:
        return cached
    if ai_rate_limited(
        user,
        scope="catalog",
        guest_config_key="CATALOG_AI_GUEST_RATE_LIMIT_PER_MINUTE",
        auth_config_key="CATALOG_AI_AUTH_RATE_LIMIT_PER_MINUTE",
    ):
        raise AiProviderError("catalog AI rate limited", code="provider_rate_limited")
    config = dict(current_app.config)
    config["AI_CONNECT_TIMEOUT_SECONDS"] = float(config.get("CATALOG_AI_CONNECT_TIMEOUT_SECONDS", 2))
    config["AI_READ_TIMEOUT_SECONDS"] = float(config.get("CATALOG_AI_READ_TIMEOUT_SECONDS", 4))
    client = get_ai_client(config)
    completion = client.complete(
        [
            {
                "role": "system",
                "content": (
                    "你是 HealthDoc 目录搜索意图解析器。只把用户搜索词转换为 JSON，不回答问题，"
                    "不猜测机构或套餐名称。字段必须是 intent_summary、audience_terms、health_topics、"
                    "location_terms、package_terms、budget_max；四个 terms 字段均为短字符串数组，"
                    "budget_max 无明确预算时为 null。不得输出健康诊断。"
                ),
            },
            {"role": "user", "content": query},
        ],
        json_output=True,
        max_tokens=260,
    )
    try:
        intent = CatalogIntent.model_validate(json.loads(completion.content))
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise AiProviderError(
            "catalog AI returned invalid intent",
            code="provider_invalid_response",
            retryable=False,
        ) from exc
    _cache_put(query, intent)
    return intent


def _terms_from_model(intent):
    values = intent.audience_terms + intent.health_topics + intent.location_terms + intent.package_terms
    return [SearchTerm(value, False) for value in _unique(values) if len(str(value).strip()) <= 40]


def _suggestions(matches, limit=8):
    rows = []
    for org_match in matches:
        organization = org_match["organization"]
        rows.append({
            "kind": "organization",
            "organization_id": organization.id,
            "institution_id": None,
            "package_id": None,
            "title": organization.name,
            "subtitle": f"{len(org_match['branches'])} 家相关分院",
            "reason": "、".join(org_match["reasons"][:2]),
            "_score": org_match["score"],
        })
        for branch_match in org_match["branches"]:
            branch = branch_match["branch"]
            rows.append({
                "kind": "branch",
                "organization_id": organization.id,
                "institution_id": branch.id,
                "package_id": None,
                "title": f"{organization.name} · {branch.branch_name}",
                "subtitle": f"{branch.district} · {branch.address}",
                "reason": "、".join(branch_match["reasons"][:2]),
                "_score": branch_match["score"] - 1,
            })
            for package_match in branch_match["matched_packages"][:2]:
                package = package_match["package"]
                rows.append({
                    "kind": "package",
                    "organization_id": organization.id,
                    "institution_id": branch.id,
                    "package_id": package.id,
                    "title": package.name,
                    "subtitle": f"{organization.name} · {branch.branch_name} · ¥{float(package.price):g}",
                    "reason": "、".join(package_match["reasons"][:2]),
                    "_score": package_match["score"] - 2,
                })
    kind_order = {"organization": 0, "branch": 1, "package": 2}
    rows.sort(key=lambda item: (-item["_score"], kind_order[item["kind"]], item["organization_id"], item["institution_id"] or 0, item["package_id"] or 0))
    result = []
    seen = set()
    for row in rows:
        key = (row["kind"], row["organization_id"], row["institution_id"], row["package_id"])
        if key in seen:
            continue
        seen.add(key)
        row = dict(row)
        row.pop("_score", None)
        result.append(row)
        if len(result) >= limit:
            break
    return result


def run_catalog_search(query: str, *, mode: str = "content", user=None):
    term = str(query or "").strip()[:80]
    organizations = (
        Organization.query.options(
            selectinload(Organization.branches).selectinload(Institution.images),
            selectinload(Organization.branches)
            .selectinload(Institution.packages)
            .selectinload(Package.versions)
            .selectinload(PackageVersion.domains)
            .selectinload(PackageVersionDomain.domain),
        )
        .filter(Organization.is_active.is_(True))
        .order_by(Organization.id)
        .all()
    )
    if not term:
        matches = []
        for organization in organizations:
            branches = [
                {"branch": branch, "score": 0, "reasons": [], "matched_packages": []}
                for branch in organization.branches
                if branch.is_active and branch.operations_suspended_at is None
            ]
            if branches:
                matches.append({"organization": organization, "score": 0, "reasons": [], "branches": branches})
        return {
            "matches": matches,
            "search": {"mode": "content", "query": "", "intent_summary": "", "needs_ai": False, "suggestions": []},
        }

    terms, budget_max, labels, complex_query = _content_intent(term)
    matches = _evaluate(organizations, terms, budget_max)
    top_score = matches[0]["score"] if matches else 0
    needs_ai = complex_query or top_score < 70
    resolved_mode = "content"
    intent_summary = "、".join(labels) if labels else term
    if mode == "hybrid" and needs_ai:
        try:
            intent = _interpret_with_model(term, user=user)
            model_terms = _terms_from_model(intent)
            merged_terms = terms + [item for item in model_terms if _fold(item.value) not in {_fold(term.value) for term in terms}]
            merged_budget = intent.budget_max if intent.budget_max is not None else budget_max
            matches = _evaluate(organizations, merged_terms, merged_budget)
            intent_summary = intent.intent_summary or intent_summary
            resolved_mode = "hybrid"
        except (AiConfigurationError, AiProviderError):
            resolved_mode = "content_fallback"
    return {
        "matches": matches,
        "search": {
            "mode": resolved_mode,
            "query": term,
            "intent_summary": intent_summary,
            "needs_ai": needs_ai and resolved_mode == "content",
            "suggestions": _suggestions(matches),
        },
    }
