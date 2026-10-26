param([ValidateSet('M01','M02','M03','M04','M05','M06','M07')][string]$Module = 'M01')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$stamp = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'China Standard Time').ToString('yyyyMMdd-HHmmss')
$batch = $stamp + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
$reportRoot = if ($env:BA_REPORT_ROOT) { $env:BA_REPORT_ROOT } else { Join-Path $root 'docs/验收报告' }
$report = Join-Path $reportRoot "$Module/$batch"
New-Item -ItemType Directory -Force $report | Out-Null
$env:PYTHONUTF8 = '1'
try {
    & uv run --locked python scripts/verify.py $Module $report
    $result = $LASTEXITCODE
    if (-not (Test-Path (Join-Path $report '验收报告.md'))) { throw 'Verification runner did not produce a final report.' }
} catch {
    $failure = @"
# $Module 验收报告

状态：**验收未通过**。批次：$batch；上海时间：$stamp。
未能运行 Python、uv 或验收入口，必验项未完成，没有将缺环境记为通过。
环境：$([Environment]::OSVersion.VersionString)；逻辑 CPU $([Environment]::ProcessorCount)。此失败记录路径不发起模型调用，先前调用情况以已有记录为准。
恢复 README 所列运行环境后重新执行 scripts/verify.ps1 -Module $Module。
原型及跨模块联调尚待本次验证；既有实现与结果以项目规划和历史最终报告为准。
环境失败记录随本最终报告保存；M07 另保存机器可读结果。
"@
    [IO.File]::WriteAllText((Join-Path $report '验收报告.md'), $failure, [Text.UTF8Encoding]::new($false))
    if ($Module -eq 'M07') {
        $metadata = @{
            module_id = 'M07'; run_id = $batch; module_verdict = '验收未通过'
            scope = 'AC-01—AC-58; NFR-01—NFR-09'; checks = @(@{ id='environment'; status='failed' })
            pending_integrations = @('环境或入口不可用；全部必验项未完成')
        } | ConvertTo-Json -Depth 8
        [IO.File]::WriteAllText((Join-Path $report '测试结果.json'), $metadata, [Text.UTF8Encoding]::new($false))
    }
    $index = Join-Path $reportRoot '索引.md'
    [IO.File]::AppendAllText($index, "`n- [$Module $batch — 验收未通过](./$Module/$batch/验收报告.md)；环境或入口不可用。`n", [Text.UTF8Encoding]::new($false))
    $result = 1
}
Write-Output "Report: $report/验收报告.md"
exit $result
