import os
import subprocess
from pathlib import Path

import pytest


def test_m07_matrix_has_no_missing_or_dangling_acceptance_cases():
    import ast

    root = Path(__file__).resolve().parents[2]
    module = ast.parse((root / "scripts/verify_m07.py").read_text(encoding="utf-8"))
    mapping = next(
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "AC_TESTS" for t in node.targets)
    )
    rows = [line.split() for line in mapping.strip().splitlines()]
    assert [row[0] for row in rows] == [f"{number:02}" for number in range(1, 59)]
    functions = {
        node.name
        for path in (root / "backend/tests").glob("test_*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert all(row[1:] and set(row[1:]) <= functions for row in rows)


@pytest.mark.parametrize("module", ["M01", "M07"])
def test_missing_tool_still_produces_failure_report(tmp_path, module):
    root = Path(__file__).resolve().parents[2]
    reports = tmp_path / "reports" / module
    before = set(reports.glob("*"))
    shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    environment = {**os.environ, "PATH": str(tmp_path), "BA_REPORT_ROOT": str(reports.parent)}
    response = subprocess.run(
        [str(shell), "-NoProfile", "-File", str(root / "scripts/verify.ps1"), "-Module", module],
        cwd=root,
        env=environment,
        capture_output=True,
        timeout=30,
    )
    assert response.returncode != 0
    created = set(reports.glob("*")) - before
    assert len(created) == 1
    report = created.pop()
    assert {item.name for item in report.iterdir()} == (
        {"验收报告.md", "测试结果.json"} if module == "M07" else {"验收报告.md"}
    )
    content = (report / "验收报告.md").read_text(encoding="utf-8")
    assert "验收未通过" in content and "没有将缺环境记为通过" in content
    assert report.name in (reports.parent / "索引.md").read_text(encoding="utf-8")
