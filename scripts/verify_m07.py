"""Full delivery verification; failed or absent evidence never counts as passed."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo

from verify import ROOT, redact_output

# Each row names the executable evidence required for that acceptance case.
AC_TESTS = """
01 test_real_oidc_roles_and_cookie test_callback_rejects_tampering
02 test_common_role_and_owner_guards test_attachment_boundary_parse_options_download_and_isolation test_ten_users_real_websockets_three_workers_and_isolation
03 test_history_fixed_boundary_source_lock_and_deleted_snapshots
04 test_attachment_boundary_parse_options_download_and_isolation test_xlsx_shape_header_external_entities_and_real_expansion_limits
05 test_send_atomic_idempotency_guards_and_snapshot test_send_validation_and_final_scope test_errors_have_request_id_and_bound_body
06 test_ten_users_real_websockets_three_workers_and_isolation
07 test_browser_periodic_snapshot_recovers_dropped_events
08 test_queue_three_fifo_cancel_prefix_and_restart
09 test_websocket_subscription_broadcast_single_use_and_account_disable test_browser_periodic_snapshot_recovers_dropped_events
10 test_context_summary_persists_and_keeps_recent_history
11 test_query_budget_and_evidence_integrity test_independent_funnel_and_payment_boundaries
12 test_sanitized_provider_errors test_stream_completion_and_interruption test_real_sandbox_timeout_memory_and_disk_limits
13 test_query_budget_and_evidence_integrity
14 test_attachment_task_real_inputs_exports_and_cleanup
15 test_archive_blocking_order_and_list_pagination test_formula_failure_task_busy_and_batch_authorization
16 test_attachment_task_real_inputs_exports_and_cleanup test_export_interruption_and_cleanup_retry
17 test_queued_task_revalidates_source_and_user test_execution_snapshot_contract
18 test_samples_isolation_queries_and_publication test_sql_rejects_out_of_scope_and_effects test_real_sandbox_stats_isolation_and_cancel
19 test_config_concurrency_validation_reload_and_logs test_endpoint_normalization_and_rejection
20 test_samples_isolation_queries_and_publication
21 test_independent_funnel_and_payment_boundaries
22 test_independent_funnel_and_payment_boundaries
23 test_worker_restart_fails_legacy_running_task test_real_sandbox_timeout_memory_and_disk_limits test_storage_refusal_preserves_existing_files
24 test_queue_three_fifo_cancel_prefix_and_restart test_attachment_task_real_inputs_exports_and_cleanup
25 test_config_concurrency_validation_reload_and_logs test_attachment_task_real_inputs_exports_and_cleanup
26 test_attachment_task_real_inputs_exports_and_cleanup test_real_sandbox_stats_isolation_and_cancel
27 test_browser_periodic_snapshot_recovers_dropped_events test_websocket_subscription_broadcast_single_use_and_account_disable
28 test_evidence_shared_contract_and_control_frame test_query_budget_and_evidence_integrity
29 test_samples_isolation_queries_and_publication test_removed_source_retains_identity
30 test_conversation_lifecycle_sources_and_guards test_send_atomic_idempotency_guards_and_snapshot
31 test_chat_browser_on_isolated_database
32 test_conversation_lifecycle_sources_and_guards test_send_validation_and_final_scope
33 test_attachment_task_real_inputs_exports_and_cleanup
34 test_attachment_task_real_inputs_exports_and_cleanup test_export_interruption_and_cleanup_retry
35 test_config_concurrency_validation_reload_and_logs test_execution_snapshot_contract test_removed_source_retains_identity
36 test_export_interruption_and_cleanup_retry test_formula_failure_task_busy_and_batch_authorization
37 test_real_sandbox_stats_isolation_and_cancel test_attachment_task_real_inputs_exports_and_cleanup
38 test_queue_three_fifo_cancel_prefix_and_restart test_worker_restart_fails_legacy_running_task test_browser_periodic_snapshot_recovers_dropped_events
39 test_send_validation_and_final_scope
40 test_send_atomic_idempotency_guards_and_snapshot test_history_fixed_boundary_source_lock_and_deleted_snapshots
41 test_context_summary_persists_and_keeps_recent_history test_query_budget_and_evidence_integrity
42 test_text_and_row_limits test_xlsx_shape_header_external_entities_and_real_expansion_limits test_real_sandbox_timeout_memory_and_disk_limits
43 test_websocket_subscription_broadcast_single_use_and_account_disable
44 test_chat_browser_on_isolated_database test_task_browser_isolated
45 test_parse_restart_barrier_and_late_callback
46 test_send_atomic_idempotency_guards_and_snapshot test_request_fingerprint_fixed_fixture
47 test_formula_failure_task_busy_and_batch_authorization test_export_interruption_and_cleanup_retry
48 test_query_budget_and_evidence_integrity test_attachment_task_real_inputs_exports_and_cleanup
49 test_formula_failure_task_busy_and_batch_authorization test_export_interruption_and_cleanup_retry
50 test_browser_periodic_snapshot_recovers_dropped_events
51 test_hidden_xlsx_formulas_and_invalid_1900_date test_formula_failure_task_busy_and_batch_authorization
52 test_independent_funnel_and_payment_boundaries
53 test_attachment_task_real_inputs_exports_and_cleanup
54 test_browser_periodic_snapshot_recovers_dropped_events test_task_browser_isolated
55 test_attachment_task_real_inputs_exports_and_cleanup
56 test_send_validation_and_final_scope test_attachment_task_real_inputs_exports_and_cleanup
57 test_csv_preserves_values_header_options_and_original_positions test_xlsx_exact_types_dates_sparse_cells_and_header
58 test_independent_funnel_and_payment_boundaries
"""


def main(directory):
    directory.mkdir(parents=True, exist_ok=True)
    evidence = directory / "证据"
    evidence.mkdir(exist_ok=True)
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT / "backend"), "BA_FUNCTIONAL_ONLY": "1"}
    checks = []
    started = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()

    def check(identifier, command, *, cwd=ROOT, timeout=1800):
        before = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            status, output = ("passed" if result.returncode == 0 else "failed"), result.stdout + result.stderr
        except (OSError, subprocess.TimeoutExpired) as error:
            status, output = "failed", f"{type(error).__name__}: required command unavailable or timed out"
        path = evidence / (identifier + ".log")
        path.write_text(redact_output(output), encoding="utf-8")
        checks.append(
            {
                "id": identifier,
                "status": status,
                "command": command,
                "seconds": round(time.monotonic() - before, 2),
                "evidence_paths": [path.relative_to(directory).as_posix()],
            }
        )
        print(f"M07 {identifier}: {status}", flush=True)
        return status == "passed"

    check("static", ["uv", "run", "--locked", "ruff", "check", "backend", "scripts"])
    check("format", ["uv", "run", "--locked", "ruff", "format", "--check", "backend", "scripts"])
    check("lock", ["uv", "lock", "--check"])
    check("contracts", [sys.executable, "scripts/export-contracts.py", "--check"])
    check(
        "backend", [sys.executable, "-m", "pytest", "-q", "--tb=short", "--junitxml=" + str(evidence / "backend.xml")]
    )
    check("build", ["npm.cmd", "run", "build"], cwd=ROOT / "frontend")
    deployed = check(
        "deployment",
        [
            "powershell",
            "-NoProfile",
            "-File",
            "scripts/deploy-compose.ps1",
        ],
    )
    if deployed:
        check("configuration", [sys.executable, "scripts/demo-m07.py", "--prepare"])
        previous_demos = ROOT / ".run/m07-demos"
        if (previous_demos / "demos.json").exists():
            if check("demos", [sys.executable, "scripts/demo-m07.py", "--verify", str(previous_demos)]):
                shutil.copytree(previous_demos, evidence / "demos")
        else:
            check("demos", [sys.executable, "scripts/demo-m07.py", str(evidence / "demos")], timeout=5400)
        package = ROOT / ".run/compose-package"
        environment.update(
            BA_COMPOSE_PACKAGE=str(package),
            BA_COMPOSE_REMOTE="/home/secops/business-attribution/compose",
            BA_UI_URL="https://192.168.10.22",
            BA_UI_ISSUER="https://192.168.10.22:9443",
            BA_UI_ACCOUNTS=str(package / ".secrets/demo-accounts.json"),
            BA_UI_OUTPUT=str(evidence / "浏览器"),
            BA_UI_DEMO_TITLE="客户行为分析 · 电脑端演示",
        )
        check("compose", [sys.executable, "scripts/verify-compose.py"])
        check(
            "browser",
            [
                "node",
                "node_modules/@playwright/test/cli.js",
                "test",
                "m01.spec.ts",
                "m02.spec.ts",
                "m03.spec.ts",
                "m05.spec.ts",
                "m06.spec.ts",
                "--grep-invert",
                "任务发送",
            ],
            cwd=ROOT / "frontend",
        )
        check("delivery", [sys.executable, "scripts/check-delivery.py", str(evidence)])

    cases = {}
    xml = evidence / "backend.xml"
    if xml.exists():
        for case in ET.parse(xml).iter("testcase"):
            cases.setdefault(case.attrib["name"].split("[")[0], []).append(
                "failed" if any(case.find(tag) is not None for tag in ("failure", "error", "skipped")) else "passed"
            )
    stage = {item["id"]: item["status"] for item in checks}
    requirements = []
    for line in AC_TESTS.strip().splitlines():
        number, *names = line.split()
        dependencies = ["backend"]
        if number in {"01", "23", "34", "36", "45"}:
            dependencies += ["compose", "delivery"]
        if number in {"07", "10", "11", "13", "14", "21", "22", "39", "48", "52", "56", "58"}:
            dependencies += ["demos"]
        if number in {"01", "14", "31", "44", "54"}:
            dependencies += ["browser"]
        passed = all(cases.get(name) and all(v == "passed" for v in cases[name]) for name in names)
        passed = passed and all(stage.get(item) == "passed" for item in dependencies)
        requirements.append(
            {
                "id": "AC-" + number,
                "status": "passed" if passed else "failed",
                "tests": names,
                "checks": dependencies,
                "evidence_paths": ["证据/backend.xml"],
            }
        )
    nfr_checks = {
        "01": ["deployment", "compose", "delivery"],
        "02": ["backend"],
        "03": ["backend", "compose", "delivery"],
        "04": ["backend", "browser"],
        "05": ["backend"],
        "06": ["backend"],
        "07": ["backend", "browser", "demos"],
        "08": ["delivery"],
        "09": ["browser", "backend"],
    }
    for number, dependencies in nfr_checks.items():
        requirements.append(
            {
                "id": "NFR-" + number,
                "status": "passed" if all(stage.get(k) == "passed" for k in dependencies) else "failed",
                "checks": dependencies,
            }
        )
    source_files = {}
    for folder in ("backend", "frontend/src", "frontend/tests", "scripts", "samples"):
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                source_files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    pending = [r["id"] for r in requirements if r["status"] != "passed"]
    passed = all(item["status"] == "passed" for item in checks) and not pending and len(requirements) == 67
    verdict = "模块验收通过" if passed else "验收未通过"
    result = {
        "module_id": "M07",
        "run_id": directory.name,
        "prd_version": "V1.5",
        "plan_version": "V1.1",
        "started_at": started,
        "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "environment": {"platform": platform.platform(), "python": platform.python_version(), "cpu": os.cpu_count()},
        "source_revision": source_files,
        "scope": "AC-01—AC-58; NFR-01—NFR-09",
        "excluded_by_user": [
            "前端页面布局、原型视觉一致性、对比度和控件尺寸；本次仅测试电脑端功能，移动端不在项目使用范围"
        ],
        "checks": checks,
        "requirements": requirements,
        "pending_integrations": pending,
        "module_verdict": verdict,
        "totals": {"passed": sum(r["status"] == "passed" for r in requirements), "failed": len(pending)},
    }
    (directory / "测试结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = "\n".join(f"| {r['id']} | {r['status']} | {', '.join(r['checks'])} |" for r in requirements)
    command_rows = "\n".join(
        f"| {r['id']} | {r['status']} | {r['seconds']} | [{r['evidence_paths'][0]}]({r['evidence_paths'][0]}) |"
        for r in checks
    )
    report = f"""# M07 集成验收与部署交付

模块结论：**{verdict}**。PRD V1.5；项目规划 V1.1；范围 AC-01—AC-58、NFR-01—NFR-09。
开始：{started}；结束：{result["finished_at"]}。源码文件摘要及逐项测试映射见 [机器结果](测试结果.json)。

## 实现与检查

新增独立 Compose 验收、执行器健康检查、十用户真实连接/隔离计算、磁盘保护与恢复边界、完整演示及逐项追踪。
模型预算本次最高66元；实际usage、保守费用估算及两条报告见证据/demos。未将受控执行器当作真实模型演示。

| 检查 | 状态 | 秒 | 证据 |
| --- | --- | --- | --- |
{command_rows}

## 逐项验收

通过 {result["totals"]["passed"]}，失败/未完成 {len(pending)}。各用例的具体函数和参数化执行结果见机器结果及backend.xml。

| 编号 | 状态 | 检查组 |
| --- | --- | --- |
{rows}

## 本次范围调整

用户明确项目仅面向电脑端，本次仅测试功能，不考虑前端页面布局。移动端不在使用范围；原型视觉一致性、布局、对比度、控件尺寸及文字缩放未纳入本次验收，未登记为通过。
浏览器证据仅用于登录、会话、附件、任务恢复、结果、导出与管理操作的功能核验；键盘、标签和状态文本仍属于功能范围。
本报告的模块结论仅覆盖本次功能范围，不代表已完成完整页面视觉验收。

## 问题与限制

待关闭：{", ".join(pending) or "无未关闭的必验项"}。
首次真实演示发现并修复SQL逻辑运算白名单误拒绝、可纠正查询错误及流式可空字段兼容；早期失败记录保留在隔离实例。
验收保留现有开发与既有Compose数据；独立项目与卷不自动删除。环境、初始化、重启和受控磁盘检查见delivery证据。
"""
    (directory / "验收报告.md").write_text(report, encoding="utf-8")
    index = directory.parents[1] / "索引.md"
    with index.open("a", encoding="utf-8") as stream:
        stream.write(
            f"\n- [M07 {directory.name} — {verdict}](M07/{directory.name}/验收报告.md)；58 AC、9 NFR逐项结果随报告保存。\n"
        )
    return 0 if passed else 1
