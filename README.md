# 经营归因分析系统

面向电脑端的多轮经营分析工作台。围绕同一个问题查询数据库、补充附件、追问并核对证据，最后生成可下载的分析报告。当前业务样例只包含 Windows/macOS 电脑，使用合成数据演示。

## 开始使用

当前交付入口：**https://192.168.10.22**。点击“通过认证中心登录”，在独立认证服务完成授权后返回工作台。

**先确认访问地址，再选择对应账号文件。两套环境账号同名，随机密码不同。**

| 环境 | 应用入口 | 独立认证 | 密码所在文件 |
| --- | --- | --- | --- |
| 虚拟机 Compose 交付 | https://192.168.10.22 | https://192.168.10.22:9443 | [.run/compose-package/.secrets/demo-accounts.json](.run/compose-package/.secrets/demo-accounts.json) |
| Windows 本地开发 | https://localhost:8443 | https://localhost:9443 | [.secrets/demo-accounts.json](.secrets/demo-accounts.json) |

| 登录名 | 登录页选择的身份 |
| --- | --- |
| `analyst_demo`、`analyst_b` | 分析用户 · analyst |
| `admin_demo` | 管理员 · admin |

密码不写入本文。每套环境均使用自己的 CA：交付环境为 `.run/compose-package/ca.crt`，开发环境为 `.secrets/ca.crt`。客户端需要信任对应证书。

## 能做什么

- 创建会话、选择只读数据源、持续追问，查看和恢复历史。
- 上传 CSV、XLSX、TXT、Markdown；查看解析结果并显式选择本轮附件。
- 查看排队、分析过程、工具调用和证据，取消任务，刷新或重连后恢复状态。
- 保存问题定义、关键指标、证据列表、分析结论、待补充数据和下一步建议。
- 显式生成最终报告，导出 Markdown/HTML，重新登录后继续下载。
- 管理员维护版本化配置、模型连接、六个数据源、工具开关及脱敏日志。

普通用户只能访问自己的会话和文件；管理员身份不授予默认跨用户业务读取权限。真实模型调用依赖部署方配置，发起分析或模型连接测试可能产生费用。

## 两条完整演示

先按 [演示步骤与数据](samples/demos/README.md) 操作。已导出的 [8 张原始业务 CSV](samples/demos/data/) 共 4594 行，补充附件可以直接下载后上传。

| 演示 | 选择的数据源 | 第三轮补充附件 | 用于核对的主要指标 |
| --- | --- | --- | --- |
| 客户行为 | 客户行为分析 | [client_error_summary.csv](samples/demos/client_error_summary.csv) | 下单转化率20%→14%；Windows下单120→60，macOS80→80 |
| 市场表现 | 市场表现分析 | [promotion_calendar.md](samples/demos/promotion_calendar.md) | 支付收入20000→16000；投放2000→2500；ROAS10→6.4 |

两条演示各进行三轮普通分析，再显式提交最终报告。每轮附件初始不选择；第三轮须等解析成功后勾选补充文件。最终报告仅汇总已保存分析，不能携带新附件或重新取数。上表预期值供人工验收，不注入运行分析提示词。

已有交付实例中可查看“客户行为分析 · 电脑端演示”和“市场表现分析 · 电脑端演示”。新增分析会调用真实模型，历史报告和导出可直接复核。

## 项目结构

```text
backend/
  app/                 业务 API、任务 worker、分析工具和共享基础
  demo_auth/           独立 OIDC 演示认证服务
  migrations/          Alembic 增量迁移
  tests/               后端及隔离集成测试
frontend/              React/TypeScript 工作台、管理页和浏览器测试
samples/
  demos/               演示步骤、补充附件及8张原始CSV
  queries/             独立复算SQL
  boundaries.json      独立边界样例
scripts/               准备、启动、部署、导出、验收和备份命令
docs/                  接口、数据字典、使用说明及历史验收证据
compose.yml            完整单机交付
compose.dev.yml        Windows开发使用的远端PostgreSQL
```

后端各文件职责和清理边界见 [backend/README.md](backend/README.md)。移出的缓存、已结束测试产物和临时前端调试脚本集中在本机 `待删除文件/`，由用户手动删除，不提交仓库。运行配置、密钥和文件保存在 `.env`、`.secrets`、`data`、`.run`，这些目录不作为源码交付；它们也不能整体按缓存删除。

源码仓库为 [Tiiauo/Business_attribution_analysis](https://github.com/Tiiauo/Business_attribution_analysis)。仓库保留运行源码、锁文件、迁移、测试、六场景种子、演示输入、原型和必要文档；不包含可用密码、模型密钥、证书私钥、安装依赖、构建目录、数据库备份或原始测试产物。现有实例的完整恢复必须另行保管配套备份，不能只克隆源码。具体范围见 [源码提交说明](docs/源码提交说明.md)。

## Windows 开发环境

项目固定使用 Python 3.12、uv、Node.js 24/npm、OpenSSH 和 tar。Docker/PostgreSQL 由现有虚拟机 `secops@192.168.10.22` 提供；首次连接前应核对 SSH 主机指纹并配置密钥或代理。

在项目根目录的 PowerShell 执行：

```powershell
# 首次准备；已有开发进程运行时，先停止再重装依赖
./scripts/prepare.ps1
./scripts/start-dev.ps1
```

准备脚本安装锁定依赖、生成本项目凭证、校验 Caddy、启动远端开发数据库和 SSH 隧道并迁移。启动脚本运行前端、API、worker、认证与本机 HTTPS 网关，并将开发 CA 加入当前用户信任存储。

| 开发组件 | 地址或端口 |
| --- | --- |
| 开发应用 / 认证 | HTTPS 8443 / 9443 |
| Vite / API / 认证内部进程 | 127.0.0.1:5174 / 8010 / 8011 |
| PostgreSQL SSH 转发 | 127.0.0.1:15432 |
| Docker API SSH 转发 | 127.0.0.1:12375 |

```powershell
./scripts/stop-dev.ps1          # 停应用进程，保留数据库和隧道
./scripts/start-dev.ps1         # 已准备环境的日常启动
```

启动前需要数据库和隧道可用；日志及进程记录在 `.run/*.log`、`.run/*.pid`。隧道故障时确认原进程退出且15432/12375空闲，再执行 `./scripts/docker-tunnel.ps1`。不通过删除卷修复连接问题。

## 数据初始化与模型配置

数据源包含商品目录、客户行为、库存、评论反馈、市场表现、退款六个场景，实际存储在专用 PostgreSQL 数据库 `ba_demo`，共25张业务表。[完整数据字典](docs/M02数据字典.md) 列出字段、关系和行数。

开发环境初始化：

```powershell
$env:PYTHONPATH = 'backend'
uv run --locked python -m app.sample_data
```

交付环境初始化：

```powershell
ssh secops@192.168.10.22 "cd /home/secops/business-attribution/compose && docker compose run --rm migrate -m app.sample_data"
```

重复导入保留已有业务记录和凭证，不清空数据库；种子行为事件的设备标识会校正为 Windows/macOS。导入后以管理员进入“系统管理”：测试数据源连接，保存需要的模型配置，再在配置总览重载整份草稿。保存草稿不会自动生效。

模型配置使用 `base_url`、模型名称和 `api_key`，支持 Chat Completions 流式工具调用。页面只展示已保存密钥的遮掩值；“测试连接”会发起最小真实请求并可能收费。详细步骤见 [配置与管理](docs/M02配置与管理.md)。

重新导出交付 CSV（需要项目依赖和已准备的 `.env`，不连接数据库或调用模型）：

```powershell
uv run --locked python scripts/export-demo-data.py
uv run --locked python scripts/export-demo-data.py --check
```

## 完整 Compose 交付

前端在 Windows 构建，虚拟机运行 db、backend、worker、idp、gateway，migrate为一次性迁移服务。

```powershell
./scripts/deploy-compose.ps1
```

默认部署项目 `ba-m01-compose`，目录 `/home/secops/business-attribution/compose`，应用443、认证9443。该命令重建并更新服务，应在无进行中任务时执行；重复部署保留密钥、账号、数据库和应用卷。数据库无公开宿主端口。

当前脚本按本项目虚拟机配置；换主机时需同步公开URL、issuer、回调地址、CA及Docker权限。完整配置见 [compose.yml](compose.yml)，环境约定见 [技术设计与环境准备](技术设计与环境准备.md)。旧8445/9445验收实例已停止，仅保留历史数据和证据。

## 检查、验收与备份

快速检查：

```powershell
uv run --locked ruff check backend scripts
uv run --locked ruff format --check backend scripts
uv run --locked python scripts/export-contracts.py --check
uv run --locked python scripts/export-demo-data.py --check
uv run --locked pytest -q backend/tests/test_demo_data.py
```

按模块执行完整验收：

```powershell
./scripts/verify.ps1 -Module M07
```

完整验收需要 Windows 服务、SSH 隧道、远端 Docker/PostgreSQL及模型配置，可能重部署和重启交付服务；真实演示会产生费用，执行前须核对维护窗口及可用预算。结果写入 `docs/验收报告/`，失败或未执行不算通过。

当前版本的部署、回归、演示与备份结果见 [最终交付验收](docs/交付验收.md)，历史记录见 [验收索引](docs/验收报告/索引.md) 和 [M07交付说明](docs/M07集成与交付.md)。报告仅证明记录中的源码版本；验收后修改需重新构建、回归和部署。本次已校正虚拟机系统时间并通过Chrony同步检查，续签网关过期的短期证书；换环境仍须核对时间和CA，不能关闭TLS校验。

备份并实际恢复到隔离环境：

```powershell
uv run --locked python scripts/backup-compose.py ba-m01-compose --restore-check
```

备份期间短暂停止本项目写入服务。备份包含数据库、角色、文件卷、配置和配套密钥，保存在VM和本机的受限目录；恢复演练不覆盖现有实例。保管、恢复步骤与实际核对结果见 [备份与恢复](docs/备份与恢复.md)。

## 常见问题与文档

- **三个账号都提示密码或身份错误**：按本文首个表格选择对应环境的账号文件，并选对身份；不要混用开发和交付密码。
- **看不到数据源或修改不生效**：确认已初始化 `ba_demo`，管理员测试并重载配置草稿。
- **无法发送分析**：检查模型及数据源有效配置、附件解析状态、会话是否归档或已有任务；错误和请求编号用于排查。
- **授权失效**：从应用入口重新发起登录，并核对应用、认证服务的时间及回调配置。

需求与设计：[PRD](PRD.md)、[项目要求](项目要求.md)、[项目规划](项目规划.md)、[前端说明](前端页面说明.md)。接口与操作：[认证接入](docs/M01接口与认证.md)、[会话与附件](docs/M03会话与历史.md)、[任务与实时](docs/M04任务与实时.md)、[分析与工具](docs/M05分析与工具.md)、[结果与清理](docs/M06结果与清理.md)。
