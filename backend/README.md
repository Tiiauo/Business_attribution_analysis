# 后端目录

后端由业务 API、异步 worker、独立演示认证和数据库迁移组成。Python 依赖及配置统一在项目根目录管理；从根目录执行命令。

| 位置 | 职责 |
| --- | --- |
| `app/main.py` | FastAPI 入口、路由、就绪检查 |
| `app/auth.py`、`demo_auth/` | 应用登录态与独立 OIDC 演示认证 |
| `app/chat.py`、`app/attachments.py` | 会话、历史及附件接口 |
| `app/tasks.py`、`app/task_events.py`、`app/worker.py` | 任务接受、调度、取消、实时通知与恢复 |
| `app/file_worker.py`、`app/attachment_parser.py` | 附件解析、导出及清理作业 |
| `app/analysis.py`、`app/model_client.py` | 真实模型调用、上下文及结构化结果 |
| `app/analysis_tools.py`、`app/sandbox.py`、`app/provenance.py` | 只读查询、文件工具、隔离计算与证据追踪 |
| `app/admin.py`、`app/runtime.py`、`app/credentials.py` | 管理接口、版本化配置与加密凭证 |
| `app/reports.py` | 固定结果导出与鉴权下载 |
| `app/models.py`、`app/contracts.py`、`app/core/` | 数据模型、共享契约、配置、数据库、鉴权基础与日志 |
| `app/bootstrap.py`、`app/sample_data.py` | 本地配置准备与六场景合成数据初始化 |
| `migrations/` | Alembic 增量迁移，保留完整迁移链 |
| `tests/` | 功能、权限、并发、解析、隔离、恢复与边界测试 |

运行入口由 [启动脚本](../scripts/start-dev.ps1) 和 [Compose](../compose.yml) 统一配置，不复制多套应用入口。详细操作见 [项目 README](../README.md)。

## 文件保留与清理

`__pycache__`、项目根目录的 `.pytest_cache` 和 `.ruff_cache` 是可重建缓存。`.run/ba_test_<12位十六进制>/` 和 `data/test/ba_test_<12位十六进制>/` 是隔离测试产物，只在测试进程结束、对应测试数据库已不存在时清理。

保留 `tests/fixtures/`、所有迁移及源码；`data/uploads`、`data/exports`、`data/workspace` 是运行数据，不能按临时文件直接删除。会话及附件清理通过业务接口完成。

`.env`、`.secrets`、`.run/compose-package`、`.run/backups`、`.run/m07-demos`、运行中的 PID/日志及 `docs/验收报告` 分别包含配置、凭证、部署包、备份、可复核演示或验收记录，不属于此次清理范围。

## 数据与验证

两条演示的原始 CSV、补充附件、口径与操作步骤见 [演示说明](../samples/demos/README.md)。数据库种子仍以 `app/sample_data.py` 为准；CSV 是可重新生成的交付快照，不是分析执行时注入的答案。

```powershell
uv run --locked ruff check backend scripts
uv run --locked python scripts/export-contracts.py --check
uv run --locked python scripts/export-demo-data.py --check
```

需要真实 PostgreSQL/Docker 的功能检查按 [模块验收入口](../docs/M07集成与交付.md) 执行；真实模型演示会产生费用。

已移出的161个目录、786个后端中间文件，以及后续移出的临时前端调试脚本，保存在本机 `待删除文件/`，按用户要求后续手动删除。清理记录保存在本机 `docs/维护记录/`，不随源码提交；仍存在测试数据库的一组临时数据保留在原位。仓库边界见 [源码提交说明](../docs/源码提交说明.md)。
