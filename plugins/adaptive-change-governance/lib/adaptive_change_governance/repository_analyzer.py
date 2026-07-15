from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .code_signals import scan_code_signals
from .file_risk import evaluate_file_risk
from .intent_model import infer_request_goal_from_text, is_low_risk_intent
from .reference_scanner import scan_references


def _keyword_hit(text_lower: str, word: str) -> bool:
    """Match a keyword against lowered text.

    ASCII keywords use word boundaries so 'api' no longer matches inside
    'therapist'; CJK keywords keep substring matching (no word boundaries).
    """
    lowered = word.lower()
    if lowered.isascii():
        return re.search(r"\b" + re.escape(lowered) + r"\b", text_lower) is not None
    return lowered in text_lower


def _empty_code_signals() -> dict[str, list]:
    return {"signals": [], "domains": [], "domain_evidence": []}


DOMAIN_KEYWORDS = {
    "financial-calculation": ["money", "amount", "price", "payment", "invoice", "billing", "refund", "settlement", "reconciliation", "金额", "价格", "支付", "账单", "发票", "退款", "结算", "对账", "两位小数"],
    "data-integrity": ["data", "record", "consistency", "state", "数据", "记录", "一致性", "状态"],
    "authentication": ["authentication", "login", "authn", "登录", "认证"],
    "authorization": ["authorization", "permission", "role", "authz", "权限", "授权"],
    "credentials": ["credential", "secret", "password", "token", "jwt", "密钥", "密码", "凭证", "令牌"],
    "privacy-data": ["privacy", "personal data", "pii", "personal information", "隐私", "个人信息", "个人数据"],
    "public-interface": ["api", "endpoint", "route", "controller", "openapi", "接口"],
    "message-contract": ["message", "event", "queue", "topic", "kafka", "消息", "事件"],
    "database-schema": ["migration", "schema", "ddl", "alter table", "create table", "drop table", "数据库", "表结构"],
    "external-system-control": ["webhook", "third-party", "external api", "provider", "外部系统", "第三方"],
    "physical-device-control": ["device command", "firmware", "protocol", "actuator", "power off", "start command", "stop command", "设备指令", "控制指令", "固件", "协议", "启动指令", "停止指令", "断电"],
}

CHANGE_TYPE_KEYWORDS = {
    "database_schema": ["migration", "schema", "ddl", "alter table", "create table", "drop table", "数据库", "表结构"],
    "message_schema": ["message", "event", "queue", "topic", "kafka", "schema", "消息", "事件"],
    "public_api": ["api", "endpoint", "route", "controller", "openapi", "接口"],
    "business_logic": ["logic", "service", "rule", "计算", "规则", "状态"],
    "configuration": ["config", "yaml", "yml", "json", "配置"],
    "documentation": ["docs", "readme", "文档", "提示文案", "文案"],
}

OPERATION_KEYWORDS = {
    "delete": ["delete", "remove", "删除"],
    "truncate": ["truncate", "清空"],
    "irreversible_migration": ["drop table", "drop column", "不可逆", "irreversible"],
    "bulk_update": ["bulk update", "批量更新", "update all", "历史数据"],
}

DATA_OPERATION_CONTEXT = [
    "data",
    "record",
    "row",
    "table",
    "database",
    "sql",
    "mapper",
    "migration",
    "schema",
    "数据",
    "记录",
    "行",
    "表",
    "数据库",
    "历史数据",
    "存量数据",
]

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}


@dataclass
class RepositoryAnalyzer:
    root: Path

    def analyze(self, request: str, project_risk: dict[str, Any], intent: dict[str, Any] | None = None) -> dict[str, Any]:
        files = self._repository_files()
        intent = intent or {}
        domain_keywords = self._merged_domain_keywords(project_risk)
        # Localization: keyword search finds candidate files; the host model's
        # relevant_files bridge the natural-language/code gap keyword search can't
        # (e.g. a Chinese request against English code). Merged, they feed code
        # signals, file risk, and reference fan-out.
        direct_files = self._merge_model_files(self._find_relevant_files(request, files), intent, files)
        tests = self._find_tests(files)
        git_info = self._git_info()
        request_domains = self._match_keywords(request, domain_keywords)
        request_domain_evidence = self._keyword_evidence(request, domain_keywords, "user_request")
        file_domains = self._domains_from_paths(direct_files, domain_keywords)
        request_goal = intent.get("request_goal") or infer_request_goal_from_text(request)
        # Display-text-only is a judgment about the intended change, which cannot be
        # read from current code and must not be guessed from request wording: a
        # keyword rule here is the one place where a literal match could *suppress*
        # risk. Only a host-model intent classification can lighten the workflow.
        text_only_change = is_low_risk_intent(intent)
        change_types = sorted(set(self._match_keywords(request, CHANGE_TYPE_KEYWORDS) + self._change_types_from_paths(direct_files)))
        if text_only_change:
            change_types = self._text_only_change_types(change_types)
        change_type_evidence = self._keyword_evidence(request, CHANGE_TYPE_KEYWORDS, "user_request")
        operations, operation_evidence = self._operation_findings(request, direct_files, text_only_change)
        # Code signals are the code-grounded domain floor (money arithmetic,
        # device protocol, route/auth decorators, message pub/sub). Skip them for
        # display-text-only changes: coarse localization can pull an unrelated file
        # into scope, and scoring its behavior would over-escalate a copy edit.
        # Destructive operations keep their relation-aware grading below.
        code_signals = scan_code_signals(self.root, [item["path"] for item in direct_files]) if not text_only_change else _empty_code_signals()
        database_changes = "database_schema" in change_types or bool({"delete", "truncate", "irreversible_migration", "bulk_update"} & set(operations))
        public_api_changes = "public_api" in change_types
        message_schema_changes = "message_schema" in change_types
        related_files = self._related_files(direct_files, files)
        unknowns = self._unknowns(direct_files, related_files, tests, git_info)
        feature_boundary = self._feature_boundary(request, direct_files, related_files, text_only_change)
        unknowns.extend(feature_boundary.get("unknowns", []))

        hint_domains = [hint["domain"] for hint in intent.get("domain_hints", [])]
        hint_domain_evidence = self._domain_hint_evidence(intent)
        affected_domains = sorted(set(request_domains + file_domains + code_signals["domains"] + hint_domains))
        affected_modules = sorted({Path(item["path"]).parts[0] for item in direct_files + related_files if Path(item["path"]).parts})
        reference_findings = scan_references(
            self.root,
            [item["path"] for item in direct_files],
            [path.relative_to(self.root).as_posix() for path in files],
        )
        file_risk_intent = dict(intent)
        if text_only_change and not file_risk_intent.get("change_nature"):
            file_risk_intent["change_nature"] = "display_text_only"
        file_risk = evaluate_file_risk(
            [item["path"] for item in direct_files + related_files],
            project_risk,
            intent=file_risk_intent,
            file_facts=feature_boundary.get("file_roles", []),
        )
        unknowns.extend(file_risk.get("constraints", []))

        return {
            "version": 1,
            "request": {
                "original": request,
                "normalized_intent": self._normalize_intent(request),
                "acceptance_criteria": self._acceptance_criteria(request),
                "model_intent": intent,
                "request_goal": request_goal,
            },
            "repository": git_info,
            "code_findings": {
                "direct_files": direct_files,
                "related_files": related_files,
                "affected_modules": affected_modules,
                "affected_domains": affected_domains,
                "reference_findings": reference_findings,
                "code_signals": code_signals["signals"],
                "change_types": change_types,
                "operations": operations,
                "domain_evidence": request_domain_evidence + self._file_keyword_evidence(direct_files, domain_keywords, "affected_domain", request) + code_signals["domain_evidence"] + hint_domain_evidence,
                "change_type_evidence": change_type_evidence + self._file_keyword_evidence(direct_files, CHANGE_TYPE_KEYWORDS, "change_type", request),
                "operation_evidence": operation_evidence,
                "database_changes": database_changes,
                "message_schema_changes": message_schema_changes,
                "public_api_changes": public_api_changes,
                "text_only_change": text_only_change,
                "feature_boundary": feature_boundary,
                "file_risk": file_risk,
                "scheduled_jobs_affected": self._path_or_request_matches(direct_files, request, ["cron", "job", "scheduler", "定时"]),
                "configuration_changes": "configuration" in change_types,
            },
            "dependency_findings": {
                "upstream": [],
                "downstream": [item["path"] for item in related_files[:20]],
                "external_dependencies": self._external_dependencies(files),
            },
            "test_findings": {
                "existing_tests": tests,
                "coverage_confidence": "low" if not tests else "medium",
                "missing_test_areas": self._missing_test_areas(affected_domains, tests),
            },
            "runtime_findings": {
                "production_usage": "unknown",
                "traffic_level": "unknown",
                "observability": self._health_label(project_risk.get("engineering_health", {}).get("observability")),
                "rollback_capability": self._health_label(project_risk.get("engineering_health", {}).get("rollback_capability")),
            },
            "unknowns": unknowns,
            "evidence_sources": ["user_request", "code_search", "git_status", "git_diff", "test_files", "project_risk_profile", "guardrails"],
        }

    def _repository_files(self) -> list[Path]:
        result = []
        for current_root, dirs, files in os.walk(self.root):
            rel_root = Path(current_root).relative_to(self.root)
            dirs[:] = [d for d in dirs if str(rel_root / d) not in SKIP_DIRS and d not in SKIP_DIRS]
            dirs[:] = [d for d in dirs if ".ai-governance/runs" not in (rel_root / d).as_posix()]
            for filename in files:
                path = Path(current_root) / filename
                if path.is_file():
                    result.append(path)
        return result

    def _find_relevant_files(self, request: str, files: list[Path]) -> list[dict[str, str]]:
        tokens = self._tokens(request)
        findings = []
        for path in files:
            rel = path.relative_to(self.root).as_posix()
            if ".ai-governance/runs/" in rel:
                continue
            score = sum(1 for token in tokens if token in rel.lower())
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                text = ""
            score += sum(1 for token in tokens if token and token in text)
            if score:
                findings.append({"path": rel, "reason": f"FACT: matched {score} request token(s) in path or content"})
        return findings[:50]

    def _merge_model_files(self, direct_files: list[dict[str, str]], intent: dict[str, Any], files: list[Path]) -> list[dict[str, str]]:
        existing = {item["path"] for item in direct_files}
        repo_paths = {path.relative_to(self.root).as_posix() for path in files}
        merged = list(direct_files)
        for item in intent.get("relevant_files", []):
            path = str(item.get("path", "")).strip()
            # Only trust model localization that points at a file actually present
            # in the repository; phantom paths are ignored, not invented into scope.
            if not path or path in existing or path not in repo_paths:
                continue
            reason = str(item.get("reason", "") or "")[:120]
            merged.append({"path": path, "reason": f"FACT: host model localized this file as relevant. {reason}".strip()})
            existing.add(path)
        return merged

    def _domain_hint_evidence(self, intent: dict[str, Any]) -> list[dict[str, str]]:
        # Model domain judgments are additive: high confidence becomes strong
        # evidence that can fire a guardrail; lower confidence is a weak candidate
        # for confirmation. Hints never remove keyword or code-signal domains.
        evidence = []
        for hint in intent.get("domain_hints", []):
            strength = "strong" if hint.get("confidence") == "high" else "weak"
            prefix = "FACT" if strength == "strong" else "WEAK SIGNAL"
            anchors = ", ".join(hint.get("anchors", [])[:5])
            location = f" at {anchors}" if anchors else ""
            reason = str(hint.get("reason", "") or "")[:160]
            evidence.append({
                "value": hint["domain"],
                "source": "model_intent",
                "keyword": "domain_hint",
                "strength": strength,
                "fact": f"{prefix}: host model classified affected_domain {hint['domain']} (confidence={hint.get('confidence')}){location}. {reason}".strip(),
            })
        return evidence

    def _related_files(self, direct_files: list[dict[str, str]], files: list[Path]) -> list[dict[str, str]]:
        stems = {Path(item["path"]).stem.lower() for item in direct_files}
        if not stems:
            return []
        related = []
        for path in files:
            rel = path.relative_to(self.root).as_posix()
            if any(item["path"] == rel for item in direct_files):
                continue
            lower = rel.lower()
            if any(stem and stem in lower for stem in stems):
                related.append({"path": rel, "reason": "INFERENCE: filename suggests relation to direct finding"})
        return related[:50]

    def _feature_boundary(
        self,
        request: str,
        direct_files: list[dict[str, str]],
        related_files: list[dict[str, str]],
        text_only_change: bool,
    ) -> dict[str, Any]:
        relation_tokens = self._relation_tokens(request)
        file_roles = []
        included = []
        weak = []
        ambiguous = []
        for item in (direct_files + related_files)[:80]:
            path = item["path"]
            text = self._safe_file_text(path)
            role = self._classify_file_role(path, text)
            relation = self._file_relation_to_request(path, text, relation_tokens)
            confidence = self._boundary_confidence(role, relation, text_only_change)
            strength = "strong" if confidence in {"high", "medium"} and relation["strength"] == "strong" else "weak"
            fact = self._boundary_fact(path, role, relation, confidence, strength)
            entry = {
                "path": path,
                "role": role,
                "confidence": confidence,
                "strength": strength,
                "matched_relation_tokens": relation["tokens"],
                "relation_mode": relation["mode"],
                "fact": fact,
            }
            file_roles.append(entry)
            if strength == "strong":
                included.append(entry)
            elif not text_only_change and role in {"database_migration", "data_access", "auth_or_permission", "public_api", "configuration", "background_job"}:
                ambiguous.append(entry)
            else:
                weak.append(entry)
        unknowns = []
        if not included:
            unknowns.append("UNKNOWN: feature boundary has no strong code ownership evidence; human or model intent confirmation is required.")
        if ambiguous:
            unknowns.append("UNKNOWN: some important files are only weakly related to the request; treat them as candidates, not confirmed change scope.")
        return {
            "target_terms": relation_tokens[:20],
            "summary": self._feature_boundary_summary(included, ambiguous, weak),
            "included_files": included[:30],
            "ambiguous_files": ambiguous[:30],
            "weak_signal_files": weak[:30],
            "file_roles": file_roles[:80],
            "unknowns": unknowns,
            "trace": [
                "FACT: feature boundary is derived from request-specific tokens, path/content matches, and file semantic role.",
                "INFERENCE: strong boundary files are better candidates for change scope than weak signal files.",
                "UNKNOWN: dynamic routing, reflection, generated code, and runtime registration can still hide dependencies.",
            ],
        }

    def _safe_file_text(self, rel_path: str) -> str:
        try:
            return (self.root / rel_path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def _classify_file_role(self, path: str, text: str) -> str:
        lower_path = path.lower()
        lower = f"{lower_path}\n{text.lower()}"
        if lower_path.startswith(("static/assets/", "dist/", "build/")) or ".min." in lower_path:
            return "generated_asset"
        if lower_path.startswith(("test/", "tests/")) or "_test." in lower_path or ".test." in lower_path or ".spec." in lower_path:
            return "test"
        if lower_path.startswith("docs/") or lower_path.endswith((".md", ".rst", ".txt")):
            return "documentation"
        if lower_path.endswith(".sql") or "migrations/" in lower_path or any(marker in lower for marker in ("alter table", "drop table", "drop column", "create table")):
            return "database_migration"
        if any(marker in lower_path for marker in ("auth", "permission", "role")) or any(marker in lower for marker in ("permission", "jwt", "token", "权限", "授权")):
            return "auth_or_permission"
        if any(marker in lower_path for marker in ("scheduler", "worker", "engine", "cron", "job")):
            return "background_job"
        if any(marker in lower_path for marker in ("database", "repository", "dao", "mapper", "models/", "persistence")) or any(marker in lower for marker in ("database_url", "sqlalchemy", "django.db", "select ", "insert ", "update ", "delete from", "query(")):
            return "data_access"
        if "/api/" in lower_path or any(marker in lower for marker in ("apirouter", "fastapi", "router.", "@route", "controller", "endpoint")):
            return "public_api"
        if any(marker in lower_path for marker in ("router", "layouts")) or any(marker in lower for marker in ("path:", "menu", "菜单")):
            return "frontend_route"
        if lower_path.startswith("frontend/src/views/") or lower_path.endswith((".vue", ".jsx", ".tsx")):
            return "ui_view"
        if any(marker in lower_path for marker in ("service", "services/")):
            return "service_logic"
        if lower_path.endswith((".yaml", ".yml", ".json", ".toml", ".ini")) or "config" in lower_path:
            return "configuration"
        return "unknown"

    def _file_relation_to_request(self, path: str, text: str, relation_tokens: list[str]) -> dict[str, Any]:
        lower_path = path.lower()
        lower_text = text.lower()
        path_tokens = [token for token in relation_tokens if token in lower_path]
        if path_tokens:
            return {"strength": "strong", "mode": "path", "tokens": path_tokens[:10]}
        for line in lower_text.splitlines():
            line_tokens = [token for token in relation_tokens if token in line]
            if line_tokens:
                return {"strength": "strong", "mode": "same_line", "tokens": line_tokens[:10]}
        content_tokens = [token for token in relation_tokens if token in lower_text]
        if content_tokens:
            return {"strength": "weak", "mode": "content_distant", "tokens": content_tokens[:10]}
        return {"strength": "weak", "mode": "none", "tokens": []}

    def _boundary_confidence(self, role: str, relation: dict[str, Any], text_only_change: bool) -> str:
        if relation["strength"] != "strong":
            return "low"
        if text_only_change and role in {"ui_view", "frontend_route", "documentation", "configuration"}:
            return "high"
        if role in {"database_migration", "data_access", "auth_or_permission", "background_job", "public_api", "service_logic", "frontend_route", "ui_view"}:
            return "high" if relation["mode"] == "path" else "medium"
        return "medium"

    def _boundary_fact(self, path: str, role: str, relation: dict[str, Any], confidence: str, strength: str) -> str:
        prefix = "FACT" if strength == "strong" else "WEAK SIGNAL"
        if relation["tokens"]:
            tokens = ", ".join(relation["tokens"][:5])
            return f"{prefix}: {path} is classified as {role}; relation to request is {relation['mode']} via token(s): {tokens}; confidence={confidence}."
        return f"{prefix}: {path} is classified as {role}, but no request-specific relation token was found; confidence={confidence}."

    def _feature_boundary_summary(
        self,
        included: list[dict[str, Any]],
        ambiguous: list[dict[str, Any]],
        weak: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "confirmed_files": len(included),
            "ambiguous_important_files": len(ambiguous),
            "weak_signal_files": len(weak),
            "confidence": "high" if included and not ambiguous else ("medium" if included else "low"),
        }

    def _find_tests(self, files: list[Path]) -> list[str]:
        tests = []
        for path in files:
            rel = path.relative_to(self.root).as_posix()
            lower = rel.lower()
            if lower.startswith("test/") or lower.startswith("tests/") or "_test." in lower or lower.endswith(".test.js") or lower.endswith(".spec.js"):
                tests.append(rel)
        return sorted(tests)

    def _git_info(self) -> dict[str, Any]:
        inside = self._git(["rev-parse", "--is-inside-work-tree"])
        if inside.returncode != 0:
            return {
                "branch": "unknown",
                "commit": "unknown",
                "dirty": True,
                "git_available": False,
                "status": "UNKNOWN: not a git repository",
                "diff_summary": [],
            }
        branch = self._git(["branch", "--show-current"]).stdout.strip() or "unknown"
        commit_result = self._git(["rev-parse", "HEAD"])
        commit = commit_result.stdout.strip() if commit_result.returncode == 0 else "unknown"
        status = self._git(["status", "--short"]).stdout.strip()
        diff = self._git(["diff", "--name-status"]).stdout.strip().splitlines()
        return {
            "branch": branch,
            "commit": commit,
            "dirty": bool(status),
            "git_available": True,
            "status": status or "clean",
            "diff_summary": diff,
        }

    def _git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], cwd=self.root, text=True, capture_output=True, check=False)

    def _tokens(self, request: str) -> list[str]:
        raw = re.findall(r"[a-zA-Z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}", request.lower())
        stop = {"the", "and", "for", "with", "一个", "修改", "调整", "修复", "需要"}
        return [token for token in raw if token not in stop]

    def _relation_tokens(self, request: str) -> list[str]:
        generic = {
            "delete",
            "remove",
            "change",
            "update",
            "config",
            "code",
            "system",
            "feature",
            "function",
            "删除",
            "移除",
            "修改",
            "调整",
            "配置",
            "代码",
            "对应",
            "功能",
            "系统",
            "管理系统",
        }
        tokens = [token for token in self._tokens(request) if token not in generic]
        return tokens or self._tokens(request)

    def _normalize_intent(self, request: str) -> str:
        return " ".join(request.strip().split())

    def _acceptance_criteria(self, request: str) -> list[str]:
        criteria = []
        if any(word in request for word in ["确保", "必须", "验收", "should", "must"]):
            criteria.append("FACT: request contains explicit requirement language; preserve stated behavior.")
        criteria.append("INFERENCE: change should satisfy the user request without introducing regression in affected modules.")
        return criteria

    def _text_only_change_types(self, change_types: list[str]) -> list[str]:
        allowed = {"documentation", "configuration"}
        filtered = [item for item in change_types if item in allowed]
        return sorted(set(filtered or ["documentation"]))

    def _match_keywords(self, text: str, mapping: dict[str, list[str]]) -> list[str]:
        lower = text.lower()
        return sorted([key for key, words in mapping.items() if any(_keyword_hit(lower, word) for word in words)])

    def _keyword_evidence(self, text: str, mapping: dict[str, list[str]], source: str, strength: str = "strong") -> list[dict[str, str]]:
        # Request-text keywords are a localization signal, not a risk verdict, so
        # domain matches from the request are recorded as weak candidates. Strong,
        # risk-driving domain evidence comes from code signals and file facts.
        lower = text.lower()
        prefix = "FACT" if strength == "strong" else "WEAK SIGNAL"
        evidence = []
        for key, words in mapping.items():
            for word in words:
                if _keyword_hit(lower, word):
                    evidence.append({
                        "value": key,
                        "source": source,
                        "keyword": word,
                        "strength": strength,
                        "fact": f"{prefix}: {source} contains keyword '{word}' mapped to {key}.",
                    })
                    break
        return evidence

    def _operation_findings(self, request: str, findings: list[dict[str, str]], text_only_change: bool = False) -> tuple[list[str], list[dict[str, str]]]:
        operations = set()
        evidence = []
        if text_only_change:
            return [], [{
                "value": "text_only_change",
                "source": "user_request",
                "keyword": "menu_or_copy",
                "strength": "strong",
                "fact": "FACT: user_request is a menu/copy display-text change; file-level delete/remove keywords are not treated as requested operations.",
            }]
        request_ops = self._keyword_evidence(request, OPERATION_KEYWORDS, "user_request")
        request_has_data_context = self._has_data_operation_context(request)
        for item in request_ops:
            operation = item["value"]
            if operation in {"truncate", "irreversible_migration", "bulk_update"} or request_has_data_context:
                operations.add(operation)
                item["fact"] = item["fact"].replace("FACT:", "FACT:", 1) + " Data/database context is present."
                evidence.append(item)
            else:
                evidence.append({
                    **item,
                    "value": "code_or_config_removal",
                    "strength": "weak",
                    "fact": f"WEAK SIGNAL: user_request contains keyword '{item['keyword']}', but no data/database context was found; not treated as destructive database operation.",
                })

        for item in self._file_keyword_evidence(findings, OPERATION_KEYWORDS, "operation", request):
            operation = item["value"]
            path = item.get("path", "")
            haystack = path
            try:
                haystack += "\n" + (self.root / path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
            if item.get("strength") == "strong" and self._is_database_operation_source(path, haystack) and (operation in {"truncate", "irreversible_migration", "bulk_update"} or self._has_data_operation_context(haystack)):
                operations.add(operation)
                evidence.append(item)
            else:
                reason = "not strongly related to this request" if item.get("strength") != "strong" else "no data/database mapping context was found"
                evidence.append({
                    **item,
                    "value": "code_or_config_removal",
                    "strength": "weak",
                    "fact": f"WEAK SIGNAL: {path} contains operation keyword '{item.get('keyword')}', but {reason}; not treated as destructive database operation.",
                })
        return sorted(operations), evidence[:50]

    def _has_data_operation_context(self, text: str) -> bool:
        lower = text.lower()
        return any(keyword.lower() in lower for keyword in DATA_OPERATION_CONTEXT)

    def _is_database_operation_source(self, path: str, text: str) -> bool:
        lower_path = path.lower()
        if lower_path.startswith(("docs/", "test/", "tests/", ".ai-governance/", "plugins/")):
            return False
        if lower_path.endswith((".md", ".rst", ".txt")):
            return False
        lower = text.lower()
        source_markers = [
            ".sql",
            ".prisma",
            ".hbm.xml",
            "schema.prisma",
            "migration",
            "migrations/",
            "alembic",
            "flyway",
            "liquibase",
            "mapper",
            "dao",
            "repository",
            "entity",
            "entities/",
            "model",
            "models/",
            "orm",
            "schema",
            "database",
            "db/",
            "sql/",
            "mybatis",
            "hibernate",
            "jpa",
            "@entity",
            "@table",
            "sequelize",
            "typeorm",
            "sqlalchemy",
            "django.db",
            "models.model",
            "active_record",
            "activerecord",
            "ecto.schema",
            "entityframework",
            "dbcontext",
            "gorm",
            "diesel",
            "doctrine",
            "mongoose",
            "mongoengine",
            "select ",
            "delete from",
            "update ",
            "alter table",
            "drop table",
            "drop column",
        ]
        return any(marker in lower_path or marker in lower for marker in source_markers)

    def _file_keyword_evidence(self, findings: list[dict[str, str]], mapping: dict[str, list[str]], source_kind: str, request: str) -> list[dict[str, str]]:
        evidence = []
        request_tokens = self._tokens(request)
        relation_tokens = self._relation_tokens(request)
        for finding in findings[:20]:
            path = self.root / finding["path"]
            haystack = finding["path"].lower()
            try:
                haystack += "\n" + path.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                pass
            matched_request_tokens = [token for token in request_tokens if token in haystack]
            for value, words in mapping.items():
                for word in words:
                    if _keyword_hit(haystack, word):
                        relation = self._keyword_relation_context(finding["path"], haystack, word, relation_tokens)
                        strength = "strong" if relation["strong"] else "weak"
                        prefix = "FACT" if strength == "strong" else "WEAK SIGNAL"
                        relation_text = self._relation_text(relation, matched_request_tokens)
                        evidence.append({
                            "value": value,
                            "source": "code_search",
                            "path": finding["path"],
                            "keyword": word,
                            "strength": strength,
                            "matched_request_tokens": matched_request_tokens[:10],
                            "matched_relation_tokens": relation["tokens"],
                            "fact": f"{prefix}: {finding['path']} contains keyword '{word}' mapped to {source_kind} {value}{relation_text}.",
                        })
                        break
        return evidence[:50]

    def _keyword_relation_context(self, path: str, haystack: str, keyword: str, relation_tokens: list[str]) -> dict[str, Any]:
        lower_path = path.lower()
        lower_keyword = keyword.lower()
        path_tokens = [token for token in relation_tokens if token in lower_path]
        if path_tokens:
            return {"strong": True, "mode": "path", "tokens": path_tokens[:10]}
        for line in haystack.splitlines():
            if lower_keyword not in line:
                continue
            line_tokens = [token for token in relation_tokens if token in line]
            if line_tokens:
                return {"strong": True, "mode": "same_line", "tokens": line_tokens[:10]}
        return {"strong": False, "mode": "distant_or_absent", "tokens": []}

    def _relation_text(self, relation: dict[str, Any], matched_request_tokens: list[str]) -> str:
        if relation["strong"] and relation["mode"] == "path":
            return f" and its path matches request-specific token(s): {', '.join(relation['tokens'][:5])}"
        if relation["strong"]:
            return f" and the keyword appears on the same line as request-specific token(s): {', '.join(relation['tokens'][:5])}"
        if matched_request_tokens:
            return f", but request token(s) {', '.join(matched_request_tokens[:5])} are not in the path or near this keyword"
        return " but does not match request-specific tokens"

    def _merged_domain_keywords(self, project_risk: dict[str, Any]) -> dict[str, list[str]]:
        merged = {key: list(values) for key, values in DOMAIN_KEYWORDS.items()}
        configured = project_risk.get("domain_keywords", {})
        if isinstance(configured, dict):
            for domain, values in configured.items():
                if isinstance(values, list):
                    merged.setdefault(domain, [])
                    merged[domain].extend(str(value) for value in values)
        return merged

    def _domains_from_paths(self, findings: list[dict[str, str]], domain_keywords: dict[str, list[str]]) -> list[str]:
        domains = []
        for finding in findings:
            path = finding["path"].lower()
            domains.extend(self._match_keywords(path, domain_keywords))
        return sorted(set(domains))

    def _change_types_from_paths(self, findings: list[dict[str, str]]) -> list[str]:
        change_types = []
        for finding in findings:
            path = finding["path"].lower()
            change_types.extend(self._match_keywords(path, CHANGE_TYPE_KEYWORDS))
            if path.endswith((".md", ".txt", ".rst")):
                change_types.append("documentation")
            if path.endswith((".yaml", ".yml", ".json", ".toml", ".ini")):
                change_types.append("configuration")
        return sorted(set(change_types))

    def _path_or_request_matches(self, findings: list[dict[str, str]], request: str, words: list[str]) -> bool:
        haystack = request.lower() + " " + " ".join(item["path"].lower() for item in findings)
        return any(word in haystack for word in words)

    def _external_dependencies(self, files: list[Path]) -> list[str]:
        names = []
        for filename in ("requirements.txt", "package.json", "Gemfile", "pyproject.toml"):
            if (self.root / filename).exists():
                names.append(filename)
        return names

    def _missing_test_areas(self, domains: list[str], tests: list[str]) -> list[str]:
        if tests:
            return []
        if domains:
            return [f"UNKNOWN: no automated tests found for affected domain {domain}" for domain in domains]
        return ["UNKNOWN: no automated tests found for request-specific behavior"]

    def _unknowns(self, direct_files: list[dict[str, str]], related_files: list[dict[str, str]], tests: list[str], git_info: dict[str, Any]) -> list[str]:
        unknowns = []
        if not direct_files:
            unknowns.append("UNKNOWN: no direct implementation files found from request keywords")
        if not related_files:
            unknowns.append("UNKNOWN: static keyword scan did not identify callers or dependents")
        if not tests:
            unknowns.append("UNKNOWN: automated regression coverage is absent or undiscovered")
        if not git_info.get("git_available"):
            unknowns.append("UNKNOWN: git metadata and diff are unavailable")
        if git_info.get("commit") == "unknown":
            unknowns.append("UNKNOWN: repository has no readable HEAD commit")
        unknowns.append("UNKNOWN: dynamic calls, framework routes, generated code, and implicit dependencies may be missed by keyword scanning")
        return unknowns

    def _health_label(self, value: Any) -> str:
        if not isinstance(value, int):
            return "unknown"
        if value <= 2:
            return "low"
        if value == 3:
            return "medium"
        return "high"
