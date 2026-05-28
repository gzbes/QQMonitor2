# QQ群聊产品型号监控系统 — 实施方案

## 背景

按照 [Requirment.md](Requirment.md) 蓝图实现完整的监控系统。当前项目仅有设计文档（`CLAUDE.md`、`Requirment.md`），尚无任何 Python 代码。系统通过 UI 自动化监控 QQ 群消息，将消息与 CSV 中的产品型号进行匹配，并在 60 秒内通知指定联系人。

**目标平台：** Windows Server 2022 + QQ NT 9.9.30+ (64-bit, Electron + C++ NT内核) + Python 3.14.5

---

## 架构概览

8 个 Python 模块 + 2 个批处理脚本，由 `main.py` 通过无限轮询循环编排，外加独立的 `watchdog.py` 守护进程：

```
main.py (主循环)
├── 加载 JSONC 配置、型号列表、初始化各模块
├── 支持 --dry-run 演习模式（通知写文件不发QQ）
├── 运行时指标收集与定期汇总输出（每10轮）
└── 无限循环：对每个群执行 采集 → 解析 → 过滤 → 增量判断 → 匹配 → 合并通知 → 冷却发送
    │
    ├── WindowManager    — 按标题找窗口句柄（优先群号）、恢复/激活/焦点（随机抖动）
    ├── QQAutomation     — 复制聊天内容（确保消息列表焦点）、发送消息给联系人
    ├── MessageParser    — 解析剪贴板文本 → 结构化消息、过滤系统/非文本消息、消息截断函数
    ├── IncrementalTracker — MD5 指纹增量去重（50,000条上限自动修剪）
    ├── ProductMatcher   — 加载 CSV 型号（自动检测编码）、不区分大小写包含匹配
    ├── NotificationService — (群, 型号, 发送者) 三元组独立冷却去重、多型号合并通知、演习模式写验证文件

watchdog.py (独立进程)
├── 每 30 秒检查 QQ.exe 是否运行 → 崩溃则重启（依赖QQ保存密码自动登录）
├── 每 30 秒检查 main.py 是否运行 → 崩溃则重启
└── QQ 自动登录失败时记录 ERROR 日志提醒运维人员
```

**每个轮询周期的数据流：** 激活群窗口 → UIA 双策略解析（当前布局优先 → 旧版兼容回退）→ 解析消息 → 过滤系统/非文本消息 → 增量指纹比对 → 型号匹配 → 多型号合并 → 冷却检查 → 发通知（或演习模式写文件）

若 UIA 双策略均失败（QQ 版本大变），回退到：四层焦点降级定位消息区 → End（滚到底部）→ Down（激活可见消息）→ 系统级 send_keys Ctrl+A/Ctrl+C → 每轮3次读剪贴板 → ...

---

## 文件创建顺序

### Phase A — 零依赖模块（可并行创建）

| 序号 | 文件 | 职责 |
|------|------|------|
| 1 | `config.jsonc` | 配置文件（JSONC）：群列表、联系人、路径、冷却时间、UI 选择器等 |
| 2 | `window_manager.py` | `find_window_by_title()` 窗口查找（优先按群号匹配）、`activate_window()` 激活恢复（随机抖动等待） |
| 3 | `message_parser.py` | 正则解析 QQ NT 聊天文本格式、过滤系统消息/非文本消息、MD5 指纹计算、消息截断函数 |
| 4 | `matcher.py` | `ProductMatcher` 类：加载 CSV（自动识别 UTF-8-BOM/GBK 编码）、不区分大小写子串匹配 |

### Phase B — 依赖 Phase A

| 序号 | 文件 | 职责 | 依赖 |
|------|------|------|------|
| 5 | `tracker.py` | `IncrementalTracker` 类：按群维护指纹集合、`get_new_messages()` 过滤已见消息 | message_parser |
| 6 | `qq_automation.py` | `QQAutomation` 类：连接 QQ 进程 (pywinauto UIA)、复制聊天内容（优先点击消息列表确保焦点）、发送消息、通过搜索打开联系人 | window_manager, config.jsonc |

### Phase C — 依赖 Phase B

| 序号 | 文件 | 职责 | 依赖 |
|------|------|------|------|
| 7 | `notifier.py` | `NotificationService` 类：(群, 型号) 冷却计时、多型号合并通知、格式化通知文本、调用 QQAutomation 发送 | qq_automation |

### Phase D — 编排层

| 序号 | 文件 | 职责 | 依赖 |
|------|------|------|------|
| 8 | `main.py` | 配置加载与校验（JSONC 注释去除）、日志初始化 (RotatingFileHandler)、信号处理 (SIGINT/SIGTERM)、无限轮询主循环、运行时指标收集与定期输出、演习模式支持 | 所有模块 |

### Phase E — 独立运维文件

| 序号 | 文件 | 职责 |
|------|------|------|
| 9 | `watchdog.py` | 通过 psutil 监控 QQ.exe 和 main.py，异常退出时自动重启 QQ（依赖保存的密码自动登录，失败则记录 ERROR） |
| 10 | `start_monitor.bat` | 启动看门狗 + 主监控（最小化窗口） |
| 11 | `stop_monitor.bat` | 停止 main.py 和 watchdog.py 进程 |

---

## 关键技术设计（超出 Requirment.md 伪代码的部分）

### 1. 可配置的 UI 选择器
pywinauto 的 `auto_id` 值（`message_list`、`input_edit`、`search_box`）可能随 QQ 版本变化。将它们外置到 `config.jsonc` → `ui_selectors`，无需修改代码即可适配不同 QQ 版本。`qq_automation.py` 中的 `_find_child()` 函数支持多选择器降级链：`auto_id` → `class_name` → `control_type` → 窗口中心点击。

### 2. CSV 编码自动检测
`matcher.py` 的 `_detect_encoding()` 先检测 BOM（UTF-8-BOM / UTF-16），再尝试 UTF-8 解码，失败则回退到 GBK。避免中文 Windows 环境下最常见的启动故障。

### 3. 消息采集剪贴板重试与退避（回退方案）
`copy_chat_content()` 优先使用 UIA 直接文本提取（见第10节）。仅当 UIA 元素不可用时回退到剪贴板方案。`_retry_clipboard_copy()` 每次尝试前先清空剪贴板，使用递增退避时间（0.5s → 1.0s → 1.5s），替代伪代码中的简单重试。

### 4. 指纹集合安全上限
`IncrementalTracker` 每组最多存储 50,000 条指纹，超出时修剪较早的一半。正常情况（聊天窗口通常仅显示最近几百条消息）不会触发，仅为长时间运行提供安全保障。

### 5. 优雅关闭
`main.py` 使用 `threading.Event` 替代全局布尔标志实现优雅关闭。信号处理器 (`SIGINT`/`SIGTERM`) 调用 `_shutdown_event.set()`。

关闭检查点分布在：
- **主循环条件**：`while not _shutdown_event.is_set()`
- **群处理循环**：每个群处理前 `break` 检查，避免继续处理剩余群
- **剪贴板重试循环**：每次重试前检查，避免 3 轮退避延迟（最多可省 6 秒）
- **剪贴板回退入口**：UIA 失败后、启动剪贴板方案前检查，跳过不必要的操作
- **轮询间隔**：1 秒子等待拆分，确保关闭延迟 ≤ 1 秒

`QQAutomation` 构造函数接受 `shutdown_event` 参数，传递给内部重试方法。

### 6. 双通道日志
- RotatingFileHandler：INFO+ 级别写入 `log_dir/monitor.log`（10MB 按大小轮转，保留 5 个备份）
- StreamHandler：WARNING+ 级别输出到 stderr
- 正常运行无控制台刷屏，异常即时可见

### 7. 多策略元素定位
`_find_child()` 优先按 `auto_id` 精确定位，失败则尝试 `control_type`/`class_name` 模糊匹配，最后回退到窗口中心点击。

### 8. 连接韧性
`QQAutomation.__init__()` 采用三策略降级连接：
1. **精确标题匹配**（`title="QQ"`）— 最可靠，QQ NT 窗口标题为精确的 `QQ` 字符串
2. **进程 PID 连接**（通过 `psutil` 查找 `QQ.exe` 进程 PID，`connect(process=pid)`）
3. **`title_re` 模式匹配**（配置中的正则，默认 `^QQ$`）

每策略 10 秒超时。全部失败则 Fatal 退出。

**背景：** 使用宽泛的 `title_re=".*QQ.*"` 会导致 `ElementAmbiguousError`，因为会匹配到 VS Code 项目名、资源管理器文件夹名等包含"QQ"字样的窗口。默认配置使用 `^QQ$` 精确匹配。

### 9. 随机抖动（Jitter）替代固定 Sleep
所有窗口操作后的等待时间使用 `random.uniform()` 随机化：
- 窗口激活后等待：0.2s ~ 0.5s
- Ctrl+A / Ctrl+C 操作间等待：0.2s ~ 0.4s
- 搜索联系人后等待：0.3s ~ 0.6s
- 群间处理间隔：以 `inter_group_delay_sec` 为中心值 ±50% 随机抖动
- 轮询间隔：以 `poll_interval_seconds` 为中心值 ±10% 随机抖动

目的：避免固定时序在特定系统环境下产生的竞态条件，同时使操作模式更接近真人行为。

### 10. 消息采集：UIA 双策略直接文本提取（主方案）
QQ NT 基于 Electron + Direct3D 渲染，其消息列表 webview 无法通过传统的"点击 → 焦点 → Ctrl+A/Ctrl+C"方式可靠获取文本。键盘快捷键在 D3D 渲染的 Electron 窗口中无法可靠定向到消息区域——即使窗口已前台激活（`SetForegroundWindow` 成功），`Ctrl+A` 仍然作用在输入框而非消息列表。

**解决方案：** 直接通过 Windows UIA 无障碍树提取消息文本。QQ NT 尽管用 D3D 渲染视觉内容，其 UIA 树完整暴露了消息结构。

**当前 QQ NT 布局（主力策略）：** 消息作为 `msg_list` 的直接 Group 子节点：
```
Window "消息列表"
  ├─ Text "2026/05/16"                     ← 日期分隔线
  ├─ Group "发送者名称" (0 子节点)           ← 新消息开始（名称=发送者，无子节点）
  ├─ Group "消息正文" (≥1 子节点)            ← 消息内容（名称=内容文本）
  ├─ Group "" (空名称 + Text 子节点)         ← 消息内容变体（富文本/多行时）
  ├─ Text "下午 14:29"                      ← 时间戳
  └─ Group "下一个发送者" ...
```

**旧版兼容布局（回退策略）：** 消息嵌套在 `ml-root` Group 下：
```
Window "消息列表"
  └─ Group "ml-root"
       ├─ Text "2026/05/16 14:29"          ← 日期分隔线
       ├─ Group "发送者名称"                 ← 发送者
       ├─ Group → Text "消息正文内容"        ← 消息内容
       └─ Group → Text "下午 14:29"         ← 时间戳
```

`_extract_messages_via_uia()` 流程：
1. 定位 `Window "消息列表"` 元素
2. **策略1** — `_parse_current_layout()`：直接遍历 `msg_list.children()`，按 Group 特征分类：有名称+0子节点→发送者、有名称+≥1子节点→内容、空名称+Text子节点→内容变体、Text→时间戳。连续空名称 Group 自动拼接为多行消息
3. **策略2**（策略1失败时）— `_try_parse_legacy()`：遍历 `ml_root.children()` 的 Group→Text 嵌套结构
4. 两种策略均失败时调用 `_dump_uia_tree()` 输出前10个子节点的诊断信息（control_type、名称、子节点数、子节点文本），辅助排查 QQ 版本变化
5. 格式化输出与剪贴板 Ctrl+A/Ctrl+C 格式完全一致，`message_parser` 无需修改

**消息列表焦点（仅剪贴板回退时使用）：** 四层降级链
1. `auto_id='message_list'` — 最精确
2. `class_name='ChatWnd'` — QQ NT 消息区类名
3. 通用 `List` control — 兜底匹配
4. 消息区坐标点击（窗口 40% 高度处，避开底部输入框）

**剪贴板回退方案：** 使用系统级 `send_keys()`（非 `dlg.type_keys()`），因为 QQ NT Electron webview 仅响应系统级键盘事件。每次 Ctrl+C 后读剪贴板 3 次应对数据到达延迟。重试 3 轮，递增退避。

### 11. 多型号合并通知
`NotificationService.send()` 将一条消息匹配到的所有型号合并在一条 QQ 消息中发送（型号用顿号分隔），而非每个型号单独发送。合并前对每个 `(群, 型号, 发送者)` 三元组独立做冷却检查，仅将未冷却的型号纳入通知。

**冷却键设计：** 冷却键从 `(群, 型号)` 升级为 `(群, 型号, 发送者)`。同一发送者在冷却期内重复提及相同型号会被跳过，但不同发送者提及相同型号不受冷却影响（各自独立计时）。这避免了以下场景的漏报：用户 A 和用户 B 在同一群内先后求购相同型号，原本的二元组冷却会导致 B 的消息被错误跳过。

### 12. 消息发送：剪贴板粘贴方案
`send_to_contact()` 使用剪贴板 + `Ctrl+V` 替代 `type_keys()` 发送消息。原因：`type_keys()` 在 QQ NT Electron 输入框中处理中文时可能出现字符丢失或编码问题，而剪贴板粘贴方案通过 `CF_UNICODETEXT` 格式设置剪贴板后再模拟 `Ctrl+V`，确保中文文本完整可靠输入。

输入框定位采用二级降级：配置的 `auto_id="input_edit"` → 通用 `Edit` 控件（取最后一个，QQ NT 聊天窗口通常只有一个大输入框）。均失败则点击窗口底部 90% 高度区域（输入框通常在底部）。

### 13. JSONC 配置支持
配置文件使用 JSONC 格式（`config.jsonc`），支持 `//` 单行注释和 `/* */` 块注释。`main.py` 加载时先用正则去除注释，再 `json.loads()` 解析。运维人员可直接在配置中添加说明注释，无需 `_comment` 伪字段。

### 14. 窗口匹配优先群号
`find_window_by_title()` 调用时优先使用群号（纯数字，更精确）匹配窗口标题，未找到时降级使用群名称匹配。QQ NT 窗口标题通常包含群号。

### 15. 消息过滤
`message_parser.parse_qq_messages()` 解析后过滤：
- **系统消息**：无发送者字段的消息（如加群/退群提示、好友通知等）
- **非文本消息**：内容仅包含 `[图片]`/`[文件]`/`[动画表情]`/`[语音]` 等占位符而无实际文本的消息
- 过滤后的消息才进入增量判断和型号匹配流程

### 16. 日志消息截断
所有日志输出中的消息内容通过 `truncate_message(text, max_len=30)` 函数处理：超过 30 字符截断并以 `...` 结尾。该函数定义在 `message_parser.py`，供所有模块调用。

### 17. 演习模式（Dry-Run）
`main.py` 支持 `--dry-run` 命令行参数：
- 正常执行窗口激活、消息采集、解析、匹配全流程
- `NotificationService` 在演习模式下不调用 `QQAutomation.send_to_contact()`，改为将通知内容写入 `dry_run_verification.log`（位于 `log_dir`）
- 冷却计时正常运作（仅抑制实际发送）
- 日志中标记 `[DRY-RUN]` 前缀

### 18. 运行时指标收集
`main.py` 主循环维护 `cycle_stats` 字典，每轮记录：消息总数、新增消息数、型号命中次数、通知发送次数、冷却跳过次数、各群处理耗时。每 10 轮输出汇总指标日志（INFO 级别），格式：
```
[指标] 第N轮 | 总消息:X 新增:Y 命中:Z 通知:W | 耗时:X.Xs | 各群: 群A=X.Xs 群B=X.Xs
```

### 19. 看门狗自动登录处理
`watchdog.py` 重启 QQ.exe 后等待 30 秒，通过 `is_qq_logged_in()` 检查 QQ 是否已登录（检查主窗口存在且标题不含"登录"字样）。QQ 客户端保存密码后可自动登录。若 30 秒后仍未检测到登录状态，记录 ERROR 日志提醒运维人员手动登录。

---

## 配置文件完整 Schema (`config.jsonc`)

配置文件使用 **JSONC 格式**（支持 `//` 单行注释和 `/* */` 块注释），程序加载时自动去除注释后解析 JSON。运维人员可直接在配置文件中添加说明注释。

```jsonc
{
    // 监控群组列表：name 为群显示名称，number 为群号（优先按群号匹配窗口标题）
    "groups": [
        {"name": "电子元器件供需群", "number": "123456789"},
        {"name": "芯片采购交流群", "number": "987654321"}
    ],
    // 接收通知的QQ联系人显示名称
    "target_contact": "采购员-李工",
    // 轮询间隔（秒），默认60
    "poll_interval_seconds": 60,
    // 产品型号CSV文件路径
    "product_csv_path": "D:\\monitor\\products.csv",
    // 日志文件目录
    "log_dir": "D:\\monitor\\logs",
    // 同型号同群同发送者通知冷却时间（秒），默认30。不同发送者独立计时
    "cooldown_sec": 30,
    // 型号匹配是否区分大小写，默认false（不区分）
    "match_case_sensitive": false,
    // 窗口/剪贴板操作重试次数
    "retry_attempts": 3,
    // 重试基础间隔（秒），实际使用递增退避
    "retry_delay_sec": 1,
    // 群间处理间隔（秒），使用随机抖动
    "inter_group_delay_sec": 5,
    // 单个日志文件最大字节数（10MB）
    "log_max_bytes": 10485760,
    // 保留的日志备份数量
    "log_backup_count": 5,
    // pywinauto 元素定位器，QQ 版本变化时调整此处即可
    "ui_selectors": {
        "main_window_title_pattern": "^QQ$",
        "message_list": {"auto_id": "message_list", "control_type": "List"},
        "message_area_fallback": {"class_name": "ChatWnd"},
        "input_edit": {"auto_id": "input_edit", "control_type": "Edit"},
        "search_box": {"auto_id": "search_box", "control_type": "Edit"},
        "contact_result_item": {"title": null, "control_type": "ListItem"},
        "fallback_to_center_click": true
    }
}
```

### 配置字段说明

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `groups` | list | 必填 | 监控群列表，每项含 `name`（按标题匹配）和 `number`（群号） |
| `target_contact` | string | 必填 | 接收通知的 QQ 联系人显示名称 |
| `poll_interval_seconds` | int | 60 | 轮询间隔（秒） |
| `product_csv_path` | string | 必填 | 产品型号 CSV 文件路径 |
| `log_dir` | string | 必填 | 日志文件目录 |
| `cooldown_sec` | int | 30 | 同型号同群同发送者通知冷却时间（秒），不同发送者独立计时 |
| `match_case_sensitive` | bool | false | 型号匹配是否区分大小写 |
| `retry_attempts` | int | 3 | 剪贴板/窗口操作重试次数 |
| `retry_delay_sec` | float | 1 | 重试基础间隔（秒），实际使用递增退避 |
| `inter_group_delay_sec` | float | 5 | 群间处理间隔（秒），使用随机抖动避免固定时序 |
| `log_max_bytes` | int | 10485760 | 单个日志文件最大字节数（10MB） |
| `log_backup_count` | int | 5 | 保留的日志备份数量 |

---

## 异常处理策略

| 异常场景 | 处理方式 |
|----------|----------|
| 群窗口找不到 | WARNING 日志，跳过本次轮询，下一轮继续查找 |
| UIA 消息列表元素缺失 / 双策略均无结果 | 自动回退到剪贴板方案，输出 UIA 树诊断信息（前10个子节点的 control_type、名称、子节点文本） |
| 剪贴板复制失败（回退方案） | 系统级 send_keys() + 每轮3次读剪贴板，重试3轮，递增退避；仍失败则 WARNING 日志跳过该群。重试循环中检查 shutdown_event |
| 联系人窗口丢失 | 尝试通过主窗口搜索重新打开；失败则 ERROR 日志，放弃本次通知 |
| 单群处理异常 | try/except 包裹，记录 ERROR，继续处理下一个群（不崩溃） |
| QQ 进程崩溃 | 看门狗检测后重启 QQ；QQ 保存密码可自动登录；若 30s 内未检测到登录状态则记录 ERROR |
| QQ 自动登录失败 | 看门狗记录 ERROR 日志提醒运维人员手动登录；主程序继续运行等待窗口恢复 |
| 主循环异常 | 捕获后记录 ERROR，continue 下一轮 |
| 产品 CSV 加载失败 | Fatal 级别，程序退出（型号列表是关键依赖） |
| QQ 连接失败（三策略全部失败） | Fatal 级别，程序退出（无法操作 QQ） |
| 用户 Ctrl+C 中断 | threading.Event 秒级响应，在重试循环、群处理循环、轮询间隔子等待中均可检测 |

---

## 验证步骤

### 0. 演习模式验证（优先）
```bash
python main.py --dry-run
```
检查 `dry_run_verification.log` 是否正确记录本应发送的通知，确认日志中有 `[DRY-RUN]` 标记，确认无实际 QQ 消息发送。

### 1. 语法验证
```bash
python -c "import window_manager; print('OK')"
python -c "import message_parser; print('OK')"
python -c "import tracker; print('OK')"
python -c "import matcher; print('OK')"
python -c "import qq_automation; print('OK')"
python -c "import notifier; print('OK')"
python -c "import main; print('OK')"
python -c "import watchdog; print('OK')"
```

### 2. 消息解析器边界测试
- 标准格式（发送者 + 日期 + 时间 + 内容）
- 仅时间格式（当天消息省略日期）
- 多条消息
- 空输入
- 系统消息过滤

### 3. 追踪器去重测试
同一批消息喂两次，第二次应该返回 0 条新消息。

### 4. 型号匹配器测试
创建示例 `products.csv`，验证加载数量和匹配结果。

### 5. 配置校验测试
测试配置文件缺失、必填字段缺失、groups 格式错误等场景。

### 6. 集成验证
```bash
python -c "import main; print('OK')"
```
确认所有模块导入链完整。

### 7. 消息截断验证
验证 `truncate_message()` 函数：短于 30 字符不变，长于 30 字符截断并追加 `...`，日志输出中的消息内容均不超过 33 字符（30 + `...`）。

### 8. 运行时指标验证
运行程序至少 10 轮，检查日志中是否出现 `[指标]` 前缀的汇总日志行，确认统计数字（总消息/新增/命中/通知）合理。

### 9. 非文本消息过滤验证
在测试群中发送图片、文件、贴纸消息，确认这些消息不出现在新增消息日志中。

### 10. 多型号合并通知验证
发送一条包含两个以上型号的消息，确认仅收到一条 QQ 通知，且通知中型号用顿号分隔。

---

## 部署后手动验证

- 先用 `python main.py --dry-run` 演习模式运行，确认 `dry_run_verification.log` 正常记录
- 在群中发送包含型号的消息，检查 60 秒内是否收到通知（通知中多个型号合并为一条）
- 发送多条同型号消息，确认 30 秒冷却期内不重复通知
- 发送图片/文件/贴纸消息，确认不会产生误报通知
- 最小化所有群窗口到任务栏，程序应能正常激活
- 断开远程桌面后重连，检查程序是否继续运行
- 检查日志中每 10 轮出现 `[指标]` 汇总行
- 长时间运行 72 小时，观察内存和 CPU 占用

---

## 实施顺序汇总

```
Phase A (并行): Task 1-4  ──┐
                             ├──→ Phase B: Task 5-6 ──→ Phase C: Task 7 ──→ Phase D: Task 8
Phase E (并行): Task 9-11 ──┘
```

- Task 1-4 之间无相互依赖，可同时创建
- Task 9-11 与任何阶段均无依赖，可随时创建
- Task 8 必须最后创建（依赖所有模块）
