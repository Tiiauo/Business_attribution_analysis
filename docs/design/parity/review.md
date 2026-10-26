# 桌面原型逐页核对与验收记录

2026-10-24。本轮仅审查电脑端，以 `前端原型图.pen` 的原有桌面设计为基线，通过 pen.dev MCP 的结构化节点读取字号、布局、边距和尺寸。代码修改范围为工作台、管理页、共享控件样式和独立授权页的页头模板；没有新增依赖，没有修改接口、数据库或实际运行配置。

按用户最新要求，前端及原型全部固定浅色，不随系统深色设置切换。原型 22 已改为浅色一致性画面，色板仅保留浅色值。

本轮桌面 UI 与交互验收通过。已逐项核对 36 个原型画面：1 个设计规范、22 个整页、4 个扩展整页、3 个弹窗、6 个状态对照板。对照板中的各操作时刻分别在真实页面中验证。完整证据见 [可切换状态、并排或叠图的逐页对照](./index.html)、[结构化清单](./acceptance.json)。每项均关联原型和当前截图。

验收基准为 1600×1000 CSS 像素；扩展表单允许纵向滚动。另外验证 1280×800、1920×1080 桌面三栏布局。正文、记录数、运行参数与按钮启用条件依照接口和当前状态显示，具体数据差异记录在各页对照说明中。字体栅格化及原生选择、日期控件保留浏览器差异。

## 已修正

| 区域 | 发现的问题 | 处理结果 |
| --- | --- | --- |
| 工作台布局 | 桌面出现手机切换栏，整体下移 36px；未选会话时结果区掉到下一行 | 桌面隐藏切换栏；始终保持三栏，顶部栏固定 64px |
| 侧栏 | 新建按钮被拉满，列表过密，选中分类没有背景 | 恢复内容宽度按钮、18px 列表间距及蓝色选中背景 |
| 对话输入 | 输入区约 232px，高于原型的 207px；新会话引导边距不一致 | 恢复 207px 输入区和 92px 输入框；新会话使用 48px 水平边距 |
| 数据源选择 | 原生选择框无法呈现原型的展开列表 | 恢复带数据库图标的六项列表、223px 列表高度、选中状态和键盘操作；未接受首问时不显示已锁定 |
| 结果区 | 普通分析直接显示大块导出表单，六部分间距过密 | 六部分按原型排列；右侧保留紧凑操作，最终报告文件分格式状态放在左侧；金额分组、负号、单位及时间格式对齐 |
| 完成与运行 | 当前轮重复显示时间、查看结果按钮，挤出过程入口和提示；运行时丢失上一轮结果 | 完成页恢复紧凑过程入口；运行中保留上轮结果，将本轮部分回答放入任务卡 |
| 分析表格 | 回答区只能显示纯文字，无法还原市场页指标表格 | 按原型的 18px 内边距、三列和行距呈现管道表格；不完整表格保留原文，HTML 作为文字显示 |
| 附件、证据 | 附件宽度类未生效；证据面板沿用 496px | 分别恢复原型中的 520px、570px |
| 删除进度 | 仍保留无关的结果侧栏 | 使用原型中的 1360px 完整内容区 |
| 管理页 | 空消息容器额外占据 22px；页签多出图标 | 去除空占位，恢复纯文字页签与边框；卡片起点约 y=324 |
| 数据源编辑 | 双列表单塞入 530px 弹窗 | 恢复原型 20 的页面内双栏卡片，返回及保存操作位于左卡；凭证按需展开 |
| 认证与工具 | 常用字段被扩展字段挤压；开关重复显示 | 高级认证配置与凭证替换按需展开；工具开关整合到状态标签，保留批准命令选择 |
| 日志 | 缺少 type 的文本框和日期输入未获得共享样式；筛选项排列偏离原型 | 统一 40px 输入框；第一行范围、时间、级别、任务、查询，第二行关联筛选 |
| 管理细节 | 字体行高和边框占位令卡片高度偏差；差异列表曾比较错版本 | 恢复 164px 版本卡、439/530/530px 工具卡；差异比较有效配置与待生效配置，冲突和校验保留本地输入 |
| 确认弹窗 | 危险按钮未复用按钮基础样式；图标与标题挤在同一行 | 统一按钮尺寸；恢复图标、标题纵向排列、530px 居中对话框及安全按钮初始焦点 |
| 市场分析 | 普通对话布局、表格缺失，无法对齐当前轮次 | 当前轮次单独展示，旧记录可向上滚动查看；表格起点 y=385、三列、192px 高，补齐 ROAS 口径说明 |
| 实时任务 | 旧消息挤占当前任务视口，工具步骤间距错误 | 当前轮次定位到顶端；三步骤任务卡高 414px，正在取消时保持发送禁用 |
| 证据状态 | 文件位置显示原始对象，缺少来源任务；工具输入原件与产物状态混淆 | 显示文件记录/行号与来源任务；分别标注生成时、当前、输入原件状态；中低强度显示待验证说明 |
| 最终报告 | 左侧误用右侧归因结论；文件和第二条证据间距偏差 | 左侧呈现该轮助手总结，右侧保留六部分；报告文件从 y=338 开始，结果操作从 y=923 开始 |
| 扩展管理 | 批准命令控件过高，无草稿的展开内容仍未对齐 | 展开命令卡高 505px；有效配置摘要高 387px，完整脱敏 JSON 可继续展开 |
| 新建数据源 | 显示空连接身份卡和错误的已配置提示 | 新建时直接填写固定身份，凭证显示待配置并必填；编辑已有源时保留固定身份卡 |
| 归档与恢复 | 查看历史按钮没有实际展开过程；断线缺少手动恢复入口 | 归档可展开只读历史；断线可重新连接，保留草稿且不自动重发问题 |
| 弹窗细节 | 多余关闭图标改变原型高度；日志缺少底部关闭动作 | 按原型保留明确的底部操作与 Escape；补齐日志脱敏元数据、内容容器和关闭按钮 |

## 原型文件调整

删除 `te7rD`、`OcHmP`、`fCd1S`，分别对应 23「窄屏 Web / 对话」、24「窄屏 Web / 六部分结果」、25「窄屏 Web / 附件面板」。原有桌面节点保留编号，避免已有引用失效；设计说明已同步为电脑端。

通过 pen.dev 在同一文件追加以下画面，复用原有 Noto Sans SC、Lucide、色板、按钮及卡片样式：

| 编号 | 节点 | 新增状态 |
| --- | --- | --- |
| 28 | `RaFbw` | 未选择会话 / 桌面欢迎 |
| 29 | `qLy9c` | 完整认证配置 / 凭证替换与高级字段展开 |
| 30 | `vz6wR` | 工具开关 / 批准命令选择 |
| 31 | `pWDnu` | 新建数据源 / 固定连接身份 |
| 32 | `RZSyw` | 删除附件 / 保留历史证据摘要 |
| 33 | `hKD5N` | 数据源测试 / 独立结果弹窗 |
| 34 | `v5ZgCl` | 无待生效草稿 / 查看有效配置 |
| 35 | `fquJY` | 管理操作 / 日志详情与移除确认片段 |
| 36 | `EalJi` | 运行日志 / 时间范围浮层展开 |
| 37 | `K5uqj` | 工具证据 / 产物与输入原件分开标注 |
| 38 | `o3efUu` | 归档历史 / 只读过程展开片段 |

当前共 36 个顶层画面，均设为浅色，未发现窄屏画面、深色画面或遗留 placeholder。全部画面已通过 pen.dev 导出到 `reference/` 并逐项目视核对。扩展表单使用更高画板容纳完整内容，浏览器页面相应允许纵向滚动。

## 验证与截图

在 `frontend` 目录执行：

```powershell
npm run build
npx playwright test tests/design-parity.spec.ts
npx playwright test tests/m01.spec.ts --grep-invert '窄屏'
```

构建通过；31 项 Chrome 桌面 UI 与交互测试通过，生成 60 张状态截图。覆盖原有主要页面及中断恢复、取消占位、归档历史、市场整页、数据库/文件/工具证据、附件清理失败、源停用、证据不足、登录失效和扩展表单。基准测试模拟系统深色偏好，并检查页面仍使用浅色。使用固定接口与实时连接夹具，不向真实模型发起请求、不发布配置、不删除真实业务数据。

另外，`m01.spec.ts` 的真实登录授权、管理员认证及错误密码、回调恢复共 3 项通过。独立授权服务已重启加载新模板，实测其页头仅显示系统名称；系统深色偏好下仍保持浅色。未提交真实分析或改写配置。

现有数据复查 6 项通过：分析用户既有会话和管理员 5 个页签。只读取已有记录，未创建分析或发布配置；未出现浏览器脚本错误或页面横向溢出。记录见 [真实页面检查](./live-checks.json)，截图位于 `after/live-*.png`。

尺寸断言包括 64px 顶栏、240px 会话栏、496px 结果栏、520px 附件栏、570px 证据栏、1360px 删除内容区、530px 删除对话框、207px 输入区、414px 运行卡、164px 版本卡、439/530/530px 工具卡、505px 批准命令展开卡、387px 配置摘要，以及市场表格和最终报告操作的位置。交互断言包括键盘选择、焦点、Escape、按格式导出状态、只读历史、草稿保留与重连不重发。

| 对照 | 修改前 | 修改后 |
| --- | --- | --- |
| 工作台 | [错误的桌面切换栏](./before/04-conversation.png) | [新会话](./after/new.png)、[分析完成](./after/success.png) |
| 管理总览 | [额外空白与页签图标](./before/07-admin-overview.png) | [配置总览](./after/admin-overview.png) |
| 工具配置 | [重复开关与过高卡片](./before/10-admin-tools.png) | [工具配置](./after/admin-工具与资源.png) |
| 其他桌面状态 | — | [证据](./after/evidence.png)、[附件](./after/attachments.png)、[删除确认](./after/delete-confirm.png)、[删除进度](./after/cleanup.png)、[系统深色时仍为浅色](./after/light-system-dark.png) |
| 管理扩展 | — | [认证完整字段](./after/admin-认证与模型.png)、[日志](./after/admin-运行日志.png)、[源编辑](./after/source-edit.png)、[新建源](./after/source-new.png) |
| 补充状态 | — | [重命名](./after/rename.png)、[附件删除](./after/attachment-delete.png)、[配置冲突](./after/admin-conflict.png)、[校验反馈](./after/admin-validation.png)、[报告准备](./after/report-prepare.png)、[指标表格](./after/market-table.png) |
| 认证页面 | — | [登录](./after/login.png)、[独立授权](./after/authorization.png)、[授权成功](./after/callback-success.png)、[授权失效](./after/callback-failure.png) |

上述画面的逐页核对、差异修复与本轮桌面验收已完成。动态正文、指标、证据、范围、清理条数与按钮状态依照当前记录显示，长度会影响纵向高度；市场页的文件证据入口仅在该轮实际存在文件证据时呈现。

本次验收范围是电脑端 UI 及关联交互，未重跑真实模型调用、真实文件上传删除、完整跨模块业务流程或其他浏览器。回答表格支持单行单元格的管道表格，其他格式保留原文。构建保留 Lucide 的 `use client` 指令提示。


## 逐画面核对清单

| 原型 | 类型 | 当前截图 | 核对内容 |
| --- | --- | --- | --- |
| [00 · 设计说明与组件](./reference/W0mGJ.png) | 规范 · 已核对 | 设计规范，无运行页面 | 浅色色板、字体、36–40px 控件规范；属于设计说明 |
| [01 · P01 授权登录入口](./reference/Z7IHA6.png) | 整页 · 已核对 | [login](./after/login.png) | 登录卡片、字号、主按钮与说明 |
| [02 · A01 演示认证授权](./reference/snP8O.png) | 整页 · 已核对 | [authorization](./after/authorization.png) | 独立授权表单与品牌页头 |
| [03 · P02 登录回调 / 状态变体](./reference/M1aRgb.png) | 状态片段 · 已核对 | [callback-pending](./after/callback-pending.png)、[callback-success](./after/callback-success.png)、[callback-failure](./after/callback-failure.png) | 三个回调时刻分别验证，原型对照板不同时出现在产品中 |
| [04 · P03 新建会话 / 选择数据源](./reference/RQO35.png) | 整页 · 已核对 | [new](./after/new.png)、[source-selected-unlocked](./after/source-selected-unlocked.png) | 六项源列表及选源前后的未锁定状态 |
| [05 · P03 工作台 / 分析完成](./reference/xo3LA.png) | 整页 · 已核对 | [success](./after/success.png) | 消息、完成过程、六部分结果和207px输入区 |
| [06 · P03 分析中 / 可取消](./reference/c1Kqt9.png) | 整页 · 已核对 | [running-full](./after/running-full.png)、[cancelling](./after/cancelling.png) | 当前轮次、414px任务卡及取消占位 |
| [07 · P03 会话附件 / 显式选择](./reference/BDlXo.png) | 整页 · 已核对 | [attachments-with-draft](./after/attachments-with-draft.png) | 520px附件栏和成功、解析中、失败三态 |
| [08 · P03 证据详情 / 来源可追溯](./reference/j83KJM.png) | 整页 · 已核对 | [evidence-file](./after/evidence-file.png)、[evidence](./after/evidence.png) | 570px证据栏、定位、历史和当前状态 |
| [09 · P03 生成最终报告 / 固定范围](./reference/G4Juo.png) | 整页 · 已核对 | [report-prepare](./after/report-prepare.png) | 固定范围说明与本轮不使用新附件；已有分析数按实际记录显示 |
| [10 · P03 最终报告 / 独立导出状态](./reference/FiN0D.png) | 整页 · 已核对 | [final](./after/final.png) | 最终报告、真实固定范围和分格式导出状态；正文由接口决定 |
| [11 · P03 已归档 / 只读与恢复](./reference/t9gBQb.png) | 整页 · 已核对 | [archived](./after/archived.png) | 归档提示、恢复入口与156px只读输入区 |
| [12 · P03 删除会话 / 二次确认](./reference/e9ssri.png) | 弹窗 · 已核对 | [delete-confirm](./after/delete-confirm.png) | 530px居中确认，安全按钮初始焦点 |
| [13 · P03 删除进度 / 可查询重试](./reference/xXeHj.png) | 整页 · 已核对 | [cleanup](./after/cleanup.png) | 1360px清理内容与单项重试 |
| [14 · 关键状态 / 错误与恢复](./reference/YwldE.png) | 状态片段 · 已核对 | [queued](./after/queued.png)、[cancelling](./after/cancelling.png)、[connection-recovery](./after/connection-recovery.png)、[failed](./after/failed.png)、[source-disabled-insufficient](./after/source-disabled-insufficient.png)、[attachment-cleanup-failed](./after/attachment-cleanup-failed.png)、[session-expired](./after/session-expired.png) | 排队、取消、断线、失败、证据不足、附件清理、停用源、登录失效 |
| [15 · P04 配置总览与重载](./reference/F0KVy.png) | 整页 · 已核对 | [admin-overview](./after/admin-overview.png) | 版本与差异、重载说明和操作 |
| [16 · P04 认证与模型配置](./reference/IkDaU.png) | 整页 · 已核对 | [admin-auth-collapsed](./after/admin-auth-collapsed.png) | 认证与模型两栏，凭证按需展开 |
| [17 · P04 数据源管理](./reference/FfPfj.png) | 整页 · 已核对 | [admin-数据源](./after/admin-数据源.png) | 六源表格、有效/草稿状态分开及原型页脚说明 |
| [18 · P04 工具与资源配置](./reference/JgdCI.png) | 整页 · 已核对 | [admin-工具与资源](./after/admin-工具与资源.png) | 439/530/530px三卡与资源单位 |
| [19 · P04 脱敏运行日志](./reference/s2IWXN.png) | 整页 · 已核对 | [admin-logs-collapsed](./after/admin-logs-collapsed.png) | 两行筛选、42px表头及46px数据行 |
| [20 · P04 编辑数据源 / 测试结果](./reference/hOVbv.png) | 整页 · 已核对 | [source-edit-tested](./after/source-edit-tested.png) | 页面内双栏编辑、固定身份和连接检查 |
| [21 · P04 配置版本冲突 / 校验失败](./reference/F4peEF.png) | 状态片段 · 已核对 | [admin-conflict](./after/admin-conflict.png)、[admin-validation](./after/admin-validation.png) | 冲突与校验是不同操作时刻；保留本地草稿，不同时显示两个错误样例 |
| [22 · P03 工作台 / 浅色外观一致性](./reference/i2XvJC.png) | 整页 · 已核对 | [light-system-dark](./after/light-system-dark.png) | 系统偏好深色时仍保持浅色 |
| [26 · P03 市场表现 / 收入与投放效率](./reference/yeXRK.png) | 整页 · 已核对 | [market-full](./after/market-full.png) | 市场标题、385px表格起点及ROAS说明；文件入口只在实际存在文件证据时出现 |
| [27 · 会话操作 / 重命名与归档规则](./reference/KErh2.png) | 状态片段 · 已核对 | [conversation-menu](./after/conversation-menu.png)、[rename](./after/rename.png)、[archive-blocked](./after/archive-blocked.png) | 菜单、488px重命名和暂不能归档 |
| [28 · P03 未选择会话 / 桌面欢迎](./reference/RaFbw.png) | 整页 · 已核对 | [none](./after/none.png) | 未选会话的欢迎状态 |
| [29 · P04 认证配置 / 完整字段与凭证替换](./reference/qLy9c.png) | 扩展整页 · 已核对 | [admin-auth-full](./after/admin-auth-full.png) | 完整认证、高级字段和凭证替换；保留可收起控件和脱敏引用 |
| [30 · P04 工具开关 / 批准命令选择](./reference/vz6wR.png) | 扩展整页 · 已核对 | [approved-commands](./after/approved-commands.png) | 505px展开命令卡，两项批准脚本可操作 |
| [31 · P04 新建数据源 / 固定连接身份](./reference/pWDnu.png) | 扩展整页 · 已核对 | [source-new](./after/source-new.png) | 新源连接身份和凭证输入 |
| [32 · P03 删除附件 / 保留历史证据摘要](./reference/RZSyw.png) | 弹窗 · 已核对 | [attachment-delete](./after/attachment-delete.png) | 原件删除范围、安全焦点和历史证据说明 |
| [33 · P04 数据源测试 / 独立结果弹窗](./reference/hKD5N.png) | 弹窗 · 已核对 | [source-test](./after/source-test.png)、[source-test-failed](./after/source-test-failed.png) | 连接检查成功与失败分开显示 |
| [34 · P04 无待生效草稿 / 查看有效配置](./reference/v5ZgCl.png) | 扩展整页 · 已核对 | [no-pending-config](./after/no-pending-config.png)、[effective-config-full](./after/effective-config-full.png) | 无待生效草稿，387px配置摘要，完整脱敏配置仍可展开 |
| [35 · P04 管理操作 / 日志详情与移除确认](./reference/fquJY.png) | 状态片段 · 已核对 | [log-detail](./after/log-detail.png)、[source-remove](./after/source-remove.png) | 脱敏日志详情与移除确认 |
| [36 · P04 日志 / 时间范围展开](./reference/EalJi.png) | 整页 · 已核对 | [log-time-range](./after/log-time-range.png) | 350px时间浮层，不改变筛选栏原始高度 |
| [37 · P03 工具证据 / 产物与输入原件分开标注](./reference/K5uqj.png) | 整页 · 已核对 | [evidence-tool_output](./after/evidence-tool_output.png) | 工具产物和输入原件当前状态分开，原型新增 |
| [38 · P03 归档历史 / 只读过程展开片段](./reference/o3efUu.png) | 状态片段 · 已核对 | [archived-history](./after/archived-history.png) | 归档历史展开仍只读，原型新增 |
