"""Run module checks; M07 also retains machine results and executable evidence."""

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)


def redact_output(value):
    for path in (
        ROOT / ".secrets/demo-accounts.json",
        ROOT / ".run/compose-package/.secrets/demo-accounts.json",
        ROOT / ".run/ba-m07-acceptance/.secrets/demo-accounts.json",
    ):
        if path.exists():
            for account in json.loads(path.read_text(encoding="utf-8")):
                value = value.replace(account["password"], "[REDACTED]")
    return re.sub(r"(?i)(code|state|websocket_token)=([^&\s\"<>]+)", r"\1=[REDACTED]", value)


def main(module, directory):
    if module == "M07":
        from verify_m07 import main as verify_m07

        return verify_m07(directory)
    directory.mkdir(parents=True, exist_ok=True)
    checks = []
    started = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT / "backend")}
    npm = "npm.cmd" if os.name == "nt" else "npm"

    def check(name, refs, command, cwd=ROOT):
        start = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=360,
            )
            status = "passed" if result.returncode == 0 else "failed"
            output = redact_output(result.stdout + result.stderr).strip()
        except (OSError, subprocess.TimeoutExpired) as error:
            status, output = "failed", f"{type(error).__name__}: required command unavailable or timed out"
        checks.append(
            {
                "name": name,
                "refs": refs,
                "command": command,
                "status": status,
                "seconds": round(time.monotonic() - start, 2),
                "output": output,
            }
        )
        print(f"{name}: {status}", flush=True)

    try:
        with tempfile.TemporaryDirectory(prefix="ba_acceptance_") as temporary:
            environment.update(
                BA_UI_URL="https://localhost:8443",
                BA_UI_ACCOUNTS=str(ROOT / ".secrets/demo-accounts.json"),
                BA_UI_OUTPUT=temporary,
            )
            if module not in {"M01", "M02", "M03", "M04", "M05", "M06"}:
                raise RuntimeError("This module has no complete implementation or verification entry yet")
            if module == "M02":
                for required in (
                    "docs/M02数据字典.md",
                    "docs/M02配置与管理.md",
                    "samples/demos/README.md",
                    "docs/design/M02/review.md",
                ):
                    if not (ROOT / required).is_file():
                        raise RuntimeError(f"Required M02 deliverable missing: {required}")
            if module == "M03":
                for required in ("docs/M03会话与历史.md", "docs/design/M03/review.md", "frontend/tests/m03.spec.ts"):
                    if not (ROOT / required).is_file():
                        raise RuntimeError(f"Required M03 deliverable missing: {required}")
            if module == "M04":
                for required in (
                    "docs/M04任务与实时.md",
                    "docs/design/M04-M05-review.md",
                    "frontend/tests/m03.spec.ts",
                ):
                    if not (ROOT / required).is_file():
                        raise RuntimeError(f"Required M04 deliverable missing: {required}")
            if module == "M05":
                for required in (
                    "docs/M05分析与工具.md",
                    "docs/design/M04-M05-review.md",
                    "frontend/tests/m03.spec.ts",
                    "frontend/tests/m05.spec.ts",
                    "scripts/smoke-analysis.py",
                    "scripts/smoke-attachment.py",
                ):
                    if not (ROOT / required).is_file():
                        raise RuntimeError(f"Required M05 deliverable missing: {required}")
            if module == "M06":
                for required in (
                    "docs/M06结果与清理.md",
                    "docs/design/M06/review.md",
                    "frontend/tests/m06.spec.ts",
                    "backend/tests/test_m06.py",
                ):
                    if not (ROOT / required).is_file():
                        raise RuntimeError(f"Required M06 deliverable missing: {required}")
            check("Python 静态检查", "工程质量", ["uv", "run", "--locked", "ruff", "check", "backend", "scripts"])
            check(
                "Python 格式", "工程质量", ["uv", "run", "--locked", "ruff", "format", "--check", "backend", "scripts"]
            )
            check("共享接口契约", "PRD 5/7/8", [sys.executable, "scripts/export-contracts.py", "--check"])
            if module == "M01":
                check("依赖锁定", "NFR-01", ["uv", "lock", "--check"])
                check("依赖完整性", "NFR-01", ["uv", "pip", "check"])
                test_groups = [
                    (
                        "真实 PostgreSQL / OIDC / 业务边界",
                        "FR-01、AC-01/02/43 模块分项",
                        [
                            "backend/tests/test_auth.py",
                            "backend/tests/test_boundaries.py",
                            "backend/tests/test_reports.py",
                        ],
                    )
                ]
            elif module == "M02":
                test_groups = [
                    (
                        "真实 PostgreSQL / OIDC / 业务边界",
                        "FR-01/05/08/09、AC-17/18/19/20/25/29/35/41/42/52/58 模块分项",
                        [
                            "backend/tests/test_admin.py",
                            "backend/tests/test_sample_edges.py",
                            "backend/tests/test_auth.py",
                            "backend/tests/test_boundaries.py",
                            "backend/tests/test_reports.py",
                        ],
                    )
                ]
            elif module == "M03":
                test_groups = [
                    (
                        "真实 PostgreSQL / OIDC / 业务边界",
                        "FR-02/03/04、AC-03/04/15/30/31/32/33/42/45/49/51/53/56 模块分项",
                        [
                            "backend/tests/test_attachments.py",
                            "backend/tests/test_files.py",
                            "backend/tests/test_chat.py",
                        ],
                    )
                ]
            elif module == "M04":
                test_groups = [
                    (
                        "任务、实时与故障边界",
                        "FR-03/06、AC-05/06/08/09/24/32/38/43/46/50/54 模块分项",
                        [
                            "backend/tests/test_tasks.py",
                            "backend/tests/test_chat.py",
                            "backend/tests/test_boundaries.py",
                            "backend/tests/test_model_client.py",
                            "backend/tests/test_analysis.py",
                        ],
                    )
                ]
            elif module == "M05":
                test_groups = [
                    (
                        "任务、模型、上下文与证据链",
                        "FR-03/05/07/08；AC-07/10/11/12/13/18/26/28/37/39/41/48/56 模块分项",
                        [
                            "backend/tests/test_model_client.py",
                            "backend/tests/test_analysis.py",
                            "backend/tests/test_tasks.py",
                            "backend/tests/test_chat.py",
                            "backend/tests/test_boundaries.py",
                        ],
                    ),
                    (
                        "附件安全输入、产物与清理联调",
                        "AC-26/37/47/48/53/56/57 模块分项",
                        ["backend/tests/test_attachments.py", "backend/tests/test_files.py"],
                    ),
                ]
            else:
                test_groups = [
                    (
                        "导出恢复、墓碑与整会话清理",
                        "FR-02/07/09；AC-16/34/36/40/49/55 模块分项",
                        ["backend/tests/test_files.py", "backend/tests/test_m06.py", "backend/tests/test_chat.py"],
                    )
                ]
            for name, refs, test_files in test_groups:
                check(
                    name,
                    refs,
                    [sys.executable, "-m", "pytest", "-q", "--tb=short", *test_files],
                )
            check("前端构建", "NFR-01", [npm, "run", "build"], ROOT / "frontend")
            if module in {"M05", "M06"}:
                check(
                    "真实模型分析链路", "FR-03/05/07、AC-07/10/11/12/39", [sys.executable, "scripts/smoke-analysis.py"]
                )
            if module == "M05":
                check("真实附件读取链路", "FR-04/05、AC-26/37/56", [sys.executable, "scripts/smoke-attachment.py"])
            browser_specs = {
                "M04": ["m03.spec.ts"],
                "M05": ["m03.spec.ts", "m05.spec.ts"],
                "M06": ["m06.spec.ts"],
            }.get(module, [f"{module.lower()}.spec.ts"])
            check(
                "浏览器交互与可访问性",
                "AC-44 模块页面、NFR-09",
                [npm, "test", "--", *browser_specs],
                ROOT / "frontend",
            )
            if module == "M01":
                check("独立 Compose 重启", "AC-01、NFR-03", [sys.executable, "scripts/verify-compose.py"])
                environment.update(
                    BA_UI_URL="https://192.168.10.22",
                    BA_UI_ACCOUNTS=str(ROOT / ".run/compose-package/.secrets/demo-accounts.json"),
                )
                check("Compose 浏览器", "AC-01/44 M01", [npm, "test", "--", "m01.spec.ts"], ROOT / "frontend")
    except Exception as error:
        checks.append(
            {
                "name": "验收入口",
                "refs": module,
                "command": [],
                "status": "failed",
                "seconds": 0,
                "output": f"{type(error).__name__}: {error}",
            }
        )

    failed = sum(item["status"] == "failed" for item in checks)
    prototype = next(ROOT.glob("*.pen"), None)
    baseline = hashlib.sha256(prototype.read_bytes()).hexdigest() if prototype else "missing"
    nodes = {
        "M01": "W0mGJ、Z7IHA6、snP8O、M1aRgb",
        "M02": "F0KVy、IkDaU、FfPfj、JgdCI、s2IWXN、hOVbv、F4peEF",
        "M03": "RQO35、t9gBQb、KErh2、te7rD、W05",
        "M04": "c1Kqt9、YwldE、xo3LA、j83KJM",
        "M05": "xo3LA、G4Juo、j83KJM、YwldE",
        "M06": "10、12、13、24、08",
    }[module]
    design = ROOT / ("docs/design/M04-M05-review.md" if module in {"M04", "M05"} else f"docs/design/{module}/review.md")
    design_review = (
        design.read_text(encoding="utf-8") if design.exists() else "未提供页面原型核对记录，页面验收待补充。"
    )
    if module == "M02":
        refs = "FR-05 源管理、FR-08、FR-09；PRD 7.4/9；NFR-05；AC-17/18/19/20/25/29/35/41/42/52/58 的 M02 分项"
        pending = "运行任务固定快照、排队源禁用与新任务权限由 M04/M05 实际联调；六场景真实模型分析、附件解析与动态双格式报告由 M03/M05/M06/M07 验收。模型未调用，费用 0 元。M02 未重复部署/重启整套 Compose；其部署验证随 M07 复核。"
    elif module == "M03":
        refs = "FR-02/03/04；PRD 6.1、7.2、8.2；NFR-03/04/07/09；AC-03/04/15/30/31/32/33/42/45/49/51/53/56 的 M03 分项"
        pending = "跨重启任务竞争、真实产物来源链和整会话清理分别由 M04/M05/M06 联调；M07 复核真实演示、电脑端键盘与标签。本次未调用外部模型，费用 0 元。"
    elif module == "M04":
        refs = "FR-03/06；PRD 6.2/6.3、8；NFR-03/04/07/09；AC-05/06/08/09/24/32/38/43/46/50/54 的 M04 分项"
        pending = "真实模型与附件工具链由 M05 复核；导出事件、整会话清理和失败重试由 M06 联调；10 用户压力、电脑端键盘与标签由 M07 汇总。"
    elif module == "M05":
        refs = (
            "FR-03/05/07/08；PRD 第 5、7、8、9 章；NFR-04/05/09；"
            "AC-07/10/11/12/13/18/26/28/37/39/41/48/53/56/57 的 M05 分项"
        )
        pending = "Markdown/HTML 物理导出、整会话清理与导出失败重试由 M06 联调；两条完整真实演示、10 用户压力、电脑端键盘与标签由 M07 汇总。"
    elif module == "M06":
        refs = "FR-02/07/09；PRD 6.1、7.3、8；NFR-03/04/07/09；AC-16/34/36/40/49/55 的 M06 分项"
        pending = "两条完整真实演示、真实磁盘耗尽、10 用户压力、电脑端键盘与标签由 M07 汇总。"
    else:
        refs = "FR-01、NFR-01/03/04 基础部分；AC-01/02/43/44 的 M01 分项"
        pending = "后续业务资源越权由 M02—M06 联调；WS 账户停用由 M04 联调；其余页面由 M07 汇总。未调用模型。"
    source = hashlib.sha256()
    for base in (ROOT / "backend", ROOT / "frontend/src", ROOT / "frontend/public", ROOT / "scripts", ROOT / "samples"):
        for path in sorted(base.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                source.update(path.relative_to(ROOT).as_posix().encode())
                source.update(hashlib.sha256(path.read_bytes()).digest())
    rows = "\n".join(
        f"| {item['name']} | {item['refs']} | {item['status']} | {item['seconds']} 秒 |" for item in checks
    )
    details = "\n\n".join(
        f"### {item['name']}\n\n命令：`{' '.join(item['command'])}`\n\n```text\n{item['output']}\n```"
        for item in checks
    )
    verdict = "模块分项检查通过" if not failed else "验收未通过"
    report = f"""# {module} 验收报告

状态：**{verdict}**。检查组通过 {len(checks) - failed}，失败 {failed}；不代表整体验收完成。
批次：{directory.name}；开始：{started}；结束：{datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}。
基线：PRD V1.5、项目规划 M01—M07；执行者 Codex。源码摘要 `{source.hexdigest()}`。
需求：{refs}。
环境：{platform.platform()}；Python {platform.python_version()}；逻辑 CPU {os.cpu_count()}；可用磁盘 {shutil.disk_usage(ROOT).free} 字节；VM PostgreSQL 16，隔离测试库，受信任 HTTPS 和系统 Chrome。

## 检查结果

| 检查 | 需求分项 | 结果 | 耗时 |
| --- | --- | --- | --- |
{rows}

## 原型对照

原型 SHA-256 `{baseline}`。参考节点：{nodes}。实际核对如下：

{design_review}

按用户要求，只保存本最终报告；不保存截图、分项日志、XML、JSON 或录制文件。浏览器断言与命令输出直接汇总到报告。

## 跨模块待验与限制

{pending}

## 执行结果

{details}
"""
    (directory / "验收报告.md").write_text(report, encoding="utf-8")
    index = directory.parents[1] / "索引.md"
    existing = index.read_text(encoding="utf-8") if index.exists() else "# 模块验收报告索引\n\n"
    relative = (directory / "验收报告.md").relative_to(index.parent).as_posix()
    index.write_text(
        existing + f"- [{module} {directory.name} — {verdict}]({relative})；只保留最终报告，后续联调见报告。\n",
        encoding="utf-8",
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], Path(sys.argv[2]).resolve()))
