## 第一部分：重新整理的需求文档

### 文档一：QQ群聊产品型号监控系统 - 需求说明文档（修订版 V2.0）

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|----------|
| V2.0 | 2026-05-27 | 架构师 | 基于V1.0优化：合并目标流程、改进增量采集方式、增加去重冷却、明确部署约束 |

---

### 1. 项目背景与目标

#### 1.1 业务目标
实现7×24小时自动监控QQ群中的产品型号求购信息，从新消息出现到通知发出总延迟不超过60秒（含轮询周期）。

#### 1.2 核心目标
- **目标1**：在Windows服务器上，手动登录一个QQ账号。开发一个应用程序，可以轮询监控QQ账号内指定的若干个群消息，**实时**提取新增消息。
- **目标2**：根据产品型号列表（CSV），对新增消息进行包含匹配。命中后立即向指定的QQ联系人发送通知，格式如下：
  ```
  监控到[群名]群里出现[型号1]、[型号2]信息：[消息发送者]-[时间]:
  原文：[消息内容的前100字符]
  ```
  （一条消息匹配多个型号时，型号用顿号分隔合并为一条通知）

#### 1.3 实现原则
- 不涉及QQ协议破解或内存注入，全程采用**模拟人工操作**（鼠标点击、键盘快捷键、剪贴板复制、窗口切换）。
- 程序本身不存储QQ账号密码，不执行登录操作。QQ客户端可通过自身"记住密码"功能在重启后自动登录。
- 程序部署在可能运行多个其他程序的Windows服务器上，需通过窗口标题精准定位群窗口和联系人窗口。

---

### 2. 用户角色

| 角色 | 职责 |
|------|------|
| 运维人员 | 手动登录QQ，启动/停止监控程序，查看日志，更新产品型号CSV（更新后重启程序） |
| 业务接收人 | 接收QQ通知消息并跟进处理 |
| 系统管理员 | 部署程序，配置开机自启（不使用Windows服务），处理服务器环境异常（如远程桌面断开后的保活） |

---

### 3. 功能性需求

#### 3.1 手动登录QQ账号
- 运维人员在服务器上手动启动QQ PC客户端，输入账号密码完成登录，保持**主窗口不最小化到托盘**（可最小化到任务栏）。
- 程序假设QQ已登录且主窗口可见。

#### 3.2 监控指定群组
- 支持配置群组列表（群名称或群号），数量1～5个（性能建议）。
- 轮询间隔可配置（默认60秒）。
- 程序按顺序激活每个群窗口，激活时需确保窗口恢复（若最小化）并获取焦点，消息区域处于可复制状态。

#### 3.3 读取产品型号CSV
- 程序启动时读取CSV文件，加载型号列表。
- CSV格式：
  ```csv
  型号,库存量
  174933-1,15000
  1-174936-1,1000
  ```
  - 首行为表头，第一列为型号（必选）。
  - 编码支持UTF-8-BOM或GBK。
- 匹配方式：**包含匹配（不区分大小写）**，型号字符串作为子串出现在消息内容中即命中。
- 型号列表无需热加载，更新CSV后重启程序生效。

#### 3.4 消息采集与增量判断（优化版）
- 对每个激活的群窗口，模拟 **`End`键滚动到底部**（确保最新消息可见），然后 **`Ctrl+A` + `Ctrl+C`** 复制当前聊天区域的全部文本。
- 解析剪贴板文本，提取每条消息的**发送者、时间、内容**。
- **消息过滤**：忽略系统消息（如加群/退群/好友提示等无发送者的系统通知）、非文本消息（如图片、文件、贴纸、语音等仅显示 `[图片]`/`[文件]`/`[动画表情]` 占位符的无文本内容消息）。仅对包含文本内容的消息做后续处理。
- **增量判断**：记录每个群**上一次成功采集的消息指纹**（指纹 = 发送者 + 完整时间戳(含日期) + 内容前100字符的MD5）。本次采集的消息与上次指纹集合比较，**仅处理新增消息**，避免重复通知。
- 程序重启后，指纹缓存清空，会重新处理当前窗口显示的所有消息（可接受，因为重启频率低）。

#### 3.5 关键词匹配
- 对每条新增消息的内容，与产品型号列表进行包含匹配（不区分大小写）。
- 若一条消息包含多个型号，全部记录并统一通知（一条通知内列出所有匹配型号）。

#### 3.6 发送通知
- 向指定的QQ联系人（单个账号）发送通知消息。
- 实现方式：通过UI自动化切换到该联系人的聊天窗口（若窗口未打开，通过QQ主界面搜索并打开），将通知文本输入输入框，模拟回车发送。
- **去重与频率限制**：
  - 同一群组的同一条消息（按消息指纹）即使匹配多个型号，也只发一次通知。
  - 对 `(群名, 型号)` 组合设置**冷却时间**（默认30秒），冷却期内即使新消息命中相同型号也不再发送，避免刷屏。
- 发送后记录日志。

#### 3.7 日志记录
- 记录INFO（正常轮询）、WARNING（窗口丢失等可恢复）、ERROR（严重错误）。
- 日志内容：程序启停、加载型号数量、每个群的轮询结果、匹配命中事件、通知发送结果、异常堆栈。
- **隐私保护**：日志中不记录完整聊天内容，仅记录群名、型号、消息摘要（截断为前30字符，超出部分以 `...` 标记）。日志输出前统一通过 `truncate_msg(msg, 30)` 函数处理消息内容字段。

---

### 4. 非功能性需求

#### 4.1 性能要求
- 单轮所有群监控总耗时 ≤ 60秒（监控≤5个群，每个群消息量≤1000条时）。
- CPU占用：空闲时 ≤5%，轮询时峰值 ≤20%。
- 内存占用 ≤ 200MB。

#### 4.2 可靠性要求
- 窗口激活失败、剪贴板操作失败时，应重试最多3次，跳过当前群并记录WARNING，继续后续群。
- 程序连续运行7天无崩溃（依赖QQ客户端稳定）。
- 提供**看门狗进程**，监控主程序及QQ进程，意外退出时自动重启。

#### 4.3 安全性要求
- 不存储QQ账号密码。
- 不向外部网络发送任何数据（除QQ协议本身外）。
- 日志中不包含敏感信息（如手机号、身份证等可选的脱敏处理由运维决定）。

#### 4.4 可观测性要求

- 程序定期（每10轮轮询）输出汇总指标日志，包含：
  - 本轮次处理消息总数、新增消息数（按群分列）
  - 型号命中次数、通知发送次数（含冷却跳过的次数）
  - 本轮轮询总耗时（秒）、各群处理耗时
  - 指纹缓存大小（每群已存储指纹数）
  - 窗口查找失败次数、剪贴板重试次数
- 指标日志级别为 INFO，与常规日志写入同一文件，便于运维排查"程序是否在正常工作"。

#### 4.5 可维护性要求
- 配置文件为JSONC格式（支持注释），可修改：群列表、目标联系人、轮询间隔、CSV路径、冷却时间、UI选择器等。
- 提供启动脚本（`start_monitor.bat`）和停止脚本（`stop_monitor.bat`）。
- 提供部署手册：环境准备、QQ窗口设置、电源与锁屏配置、远程桌面保活配置。

---

### 5. 运行环境与约束

| 项目 | 规格 |
|------|------|
| 操作系统 | Windows Server 2022（简体中文） |
| QQ版本 | NT QQ 9.9.30+ 正式版（64位，基于Electron + C++ NT内核） |
| Python | 3.14.5 |
| 显示器 | 保持桌面不锁屏（电源选项“从不关闭显示器”），分辨率任意 |
| 网络 | 可正常访问QQ服务器 |

**关键约束**：
- QQ主窗口和被监控群窗口**不能最小化到托盘**（可以最小化到任务栏）。
- 远程桌面会话断开后，需配置组策略和电源选项使会话保持活跃且不锁屏（见部署手册）。
- 服务器上不建议同时进行其他鼠标键盘干扰操作（建议专用虚拟机）。
- **QQ群成员昵称中不应包含多余空格**（如"采购 小王"应改为"采购小王"），以确保消息解析正则表达式能正确提取发送者信息。

---

## 第二部分：实现方案与架构设计

### 1. 技术选型

| 组件 | 选择 | 理由 |
|------|------|------|
| 编程语言 | Python 3.14.5 | 丰富的Windows自动化库，快速开发 |
| UI自动化核心 | `pywinauto` + `uia` backend | 支持QQ NT版（基于Electron + C++ NT内核，UI层通过UIA可访问） |
| 窗口管理 | `win32gui`, `win32con`, `win32process` (pywin32) | 激活、枚举、恢复窗口 |
| 剪贴板操作 | `win32clipboard` | 稳定可靠 |
| 键盘模拟 | `pywinauto.keyboard.send_keys` | 发送组合键 |
| 日志 | `logging` + `RotatingFileHandler` | 按大小轮转 |
| 进程看门狗 | `psutil` + `subprocess` | 监控进程并拉起 |
| 配置管理 | `json` + `dataclasses` | 简单 |

### 2. 整体架构

```
┌──────────────────────────────────────────────────────────────────┐
│                         main.py (主循环)                          │
│  - 加载JSONC配置、型号列表、初始化各模块                           │
│  - 支持 --dry-run 演习模式（通知写文件不发QQ）                     │
│  - 运行时指标收集与定期汇总（每10轮）                              │
│  - 无限循环：采集 → 解析 → 过滤 → 增量判断 → 匹配 → 合并 → 通知  │
│  - 随机抖动轮询间隔，捕获全局异常                                  │
└───────┬───────────────────────┬──────────────────────────────────┘
        │                       │
        ▼                       ▼
┌──────────────────┐    ┌──────────────────┐
│ WindowManager    │    │ MessageParser    │
│ - 按标题找句柄    │    │ - 解析QQ剪贴板文本│
│   (优先群号匹配)  │    │ - 过滤系统/非文本 │
│ - 恢复/激活窗口  │    │ - 消息指纹计算    │
│   (随机抖动等待)  │    │ - 消息截断函数    │
└──────────────────┘    └────────┬─────────┘
        │                       │
        ▼                       ▼
┌──────────────────┐    ┌──────────────────┐
│ QQAutomation     │    │ ProductMatcher   │
│ - 确保消息列表焦点│    │ - 加载CSV型号列表│
│ - 复制聊天内容    │    │ - 自动检测编码    │
│ - 发送消息给联系人│    │ - 包含匹配(不区分│
│ - 打开联系人窗口  │    │   大小写)        │
└──────────────────┘    └────────┬─────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │ Notification     │
                        │ - 多型号合并通知  │
                        │ - (群,型号)冷却   │
                        │ - 演习模式写文件  │
                        │ - 调用QQAutomation│
                        └──────────────────┘
```

### 3. 核心模块详细设计

#### 3.1 窗口管理器 (`window_manager.py`)

```python
import win32gui
import win32con
import time
import random

def find_window_by_title(substring):
    """返回第一个标题包含substring的可见窗口句柄，无则返回None"""
    hwnd = None
    def callback(hwnd, extra):
        if win32gui.IsWindowVisible(hwnd) and substring in win32gui.GetWindowText(hwnd):
            extra.append(hwnd)
    results = []
    win32gui.EnumWindows(callback, results)
    return results[0] if results else None

def activate_window(hwnd):
    """恢复并激活窗口，使用随机抖动避免固定时序导致的竞态问题"""
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(random.uniform(0.2, 0.5))  # 随机等待，避免固定时序
```

#### 3.2 QQ自动化操作 (`qq_automation.py`)

使用 `pywinauto` 连接到QQ进程，通过控件ID定位。

```python
from pywinauto import Application
from pywinauto.keyboard import send_keys
import win32clipboard
import time

class QQAutomation:
    def __init__(self):
        self.app = Application(backend="uia").connect(title_re=".*QQ.*")
    
    def copy_chat_content(self, group_hwnd):
        """激活群窗口，复制全部聊天文本"""
        activate_window(group_hwnd)
        dlg = self.app.window(handle=group_hwnd)
        # 优先尝试点击消息列表区域获取焦点，确保后续Ctrl+A作用于聊天记录而非输入框
        msg_list = dlg.child_window(auto_id="message_list", control_type="List")
        if msg_list.exists():
            msg_list.click_input()
        else:
            # 降级：尝试通过 class_name 定位消息区域
            try:
                dlg.child_window(class_name="ChatWnd").click_input()
            except:
                dlg.click_input()  # 最后降级：点击窗口中心
        time.sleep(random.uniform(0.2, 0.4))
        send_keys('^a')   # Ctrl+A 全选聊天记录
        send_keys('^c')   # Ctrl+C 复制
        time.sleep(random.uniform(0.2, 0.4))
        return self._get_clipboard_text()
    
    def _get_clipboard_text(self):
        win32clipboard.OpenClipboard()
        try:
            return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        except:
            return ""
        finally:
            win32clipboard.CloseClipboard()
    
    def send_to_contact(self, contact_name, message):
        """发送消息给指定联系人（单人窗口）"""
        hwnd = find_window_by_title(contact_name)
        if not hwnd:
            self._open_contact_via_main(contact_name)
            time.sleep(random.uniform(0.5, 1.0))
            hwnd = find_window_by_title(contact_name)
        if not hwnd:
            raise Exception(f"无法打开联系人窗口: {contact_name}")
        activate_window(hwnd)
        dlg = self.app.window(handle=hwnd)
        # 输入框通常有 auto_id "input_edit"
        input_box = dlg.child_window(auto_id="input_edit", control_type="Edit")
        input_box.click_input()
        input_box.type_keys(message, with_spaces=True)
        send_keys('{ENTER}')
    
    def _open_contact_via_main(self, contact_name):
        """通过主窗口搜索框打开联系人（简化实现）"""
        main_hwnd = find_window_by_title("QQ")
        if not main_hwnd:
            return
        activate_window(main_hwnd)
        dlg = self.app.window(handle=main_hwnd)
        search_box = dlg.child_window(auto_id="search_box", control_type="Edit")
        search_box.click_input()
        search_box.type_keys(contact_name)
        time.sleep(random.uniform(0.3, 0.6))
        result_item = dlg.child_window(title=contact_name, control_type="ListItem")
        if result_item.exists():
            result_item.double_click_input()
```

#### 3.3 消息解析与指纹 (`message_parser.py`)

```python
import re
import hashlib
from datetime import datetime

def parse_qq_messages(full_text):
    """
    解析QQ聊天记录文本。
    示例格式：
    张三 2026-05-27 14:30:12
    求购174933-1
    李四 14:32:05
    我有货
    """
    lines = full_text.strip().splitlines()
    messages = []
    current = None
    for line in lines:
        # 匹配发送者 + 日期时间 或 发送者 + 时间(无日期)
        match = re.match(r'^(.+?)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2})$', line)
        if match:
            if current and current['content']:
                messages.append(current)
            sender = match.group(1)
            time_str = match.group(2)
            if len(time_str) == 8:  # 只有时间，如 "14:32:05"
                time_str = f"{datetime.now().strftime('%Y-%m-%d')} {time_str}"
            current = {'sender': sender, 'time': time_str, 'content': ''}
        else:
            if current and line.strip():
                current['content'] += line.strip() + '\n'
    if current and current['content']:
        messages.append(current)
    return messages

def message_fingerprint(msg):
    unique_str = f"{msg['sender']}|{msg['time']}|{msg['content'][:100]}"
    return hashlib.md5(unique_str.encode('utf-8')).hexdigest()

def truncate_message(text, max_len=30):
    """截断消息内容用于日志输出，保护隐私"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."
```

#### 3.4 增量跟踪器 (`tracker.py`)

```python
class IncrementalTracker:
    def __init__(self):
        self.fingerprints = {}  # {group_name: set(fingerprints)}
    
    def get_new_messages(self, group_name, messages):
        new_msgs = []
        existing = self.fingerprints.get(group_name, set())
        for msg in messages:
            fp = message_fingerprint(msg)
            if fp not in existing:
                new_msgs.append(msg)
                existing.add(fp)
        self.fingerprints[group_name] = existing
        return new_msgs
```

#### 3.5 型号匹配器 (`matcher.py`)

```python
import csv

class ProductMatcher:
    def __init__(self, csv_path, encoding='utf-8-sig'):
        self.models = []
        with open(csv_path, 'r', encoding=encoding) as f:
            reader = csv.reader(f)
            next(reader)  # skip header
            for row in reader:
                if row:
                    self.models.append(row[0].strip().lower())
    
    def match(self, text):
        text_lower = text.lower()
        matched = [model for model in self.models if model in text_lower]
        return matched
```

#### 3.6 通知服务 (`notifier.py`)

```python
import time
from collections import defaultdict

class NotificationService:
    def __init__(self, qq_auto, target_contact, cooldown_sec=30):
        self.qq_auto = qq_auto
        self.target = target_contact
        self.cooldown = cooldown_sec
        self.last_sent = defaultdict(float)  # key: (group, model) -> timestamp
    
    def send(self, group_name, message_obj, matched_models):
        """发送通知：一条消息匹配多个型号时合并在一条通知中发送"""
        if not matched_models:
            return
        # 按冷却过滤型号
        now = time.time()
        active_models = []
        for model in matched_models:
            key = (group_name, model)
            if now - self.last_sent.get(key, 0) >= self.cooldown:
                active_models.append(model)
                self.last_sent[key] = now
        if not active_models:
            return False  # 全部在冷却期内，跳过
        # 合并为一条通知
        models_str = "、".join(active_models)
        notification = (
            f"监控到{group_name}群里出现{models_str}信息："
            f"{message_obj['sender']}-{message_obj['time']}:\n"
            f"原文：{message_obj['content'][:100]}"
        )
        self.qq_auto.send_to_contact(self.target, notification)
        return True
```

### 4. 主程序流程 (`main.py`)

```python
import json
import time
import logging
from window_manager import find_window_by_title, activate_window
from qq_automation import QQAutomation
from message_parser import parse_qq_messages, truncate_message
from tracker import IncrementalTracker
from matcher import ProductMatcher
from notifier import NotificationService

def main():
    # 加载配置（JSONC格式，自动去除注释）
    import re
    with open('config.jsonc', 'r', encoding='utf-8') as f:
        raw = f.read()
    raw = re.sub(r'//.*', '', raw)            # 去除单行注释
    raw = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)  # 去除块注释
    config = json.loads(raw)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    qq = QQAutomation()
    tracker = IncrementalTracker()
    matcher = ProductMatcher(config['product_csv_path'])
    notifier = NotificationService(qq, config['target_contact'], config.get('cooldown_sec', 30))
    
    groups = config['groups']
    poll_interval = config.get('poll_interval_seconds', 60)
    
    logging.info(f"监控程序启动，监控群组：{groups}，型号数量：{len(matcher.models)}")
    
    cycle_count = 0
    while True:
        cycle_count += 1
        cycle_start = time.time()
        cycle_stats = {"messages_total": 0, "messages_new": 0, "matches": 0, "notifications": 0, "skipped": 0}
        for group in groups:
            group_name = group['name']
            try:
                # 优先按群号匹配（更精确），降级按群名称匹配
                hwnd = find_window_by_title(group['number']) or find_window_by_title(group['name'])
                if not hwnd:
                    logging.warning(f"未找到群窗口: {group['name']}({group['number']})")
                    continue
                raw_text = qq.copy_chat_content(hwnd)
                if not raw_text:
                    continue
                all_msgs = parse_qq_messages(raw_text)
                new_msgs = tracker.get_new_messages(group_name, all_msgs)
                cycle_stats["messages_total"] += len(all_msgs)
                cycle_stats["messages_new"] += len(new_msgs)
                if new_msgs:
                    logging.info(f"群 {group_name} 发现 {len(new_msgs)} 条新消息")
                for msg in new_msgs:
                    matched = matcher.match(msg['content'])
                    if matched:
                        logging.info(f"匹配成功 群:{group_name} 型号:{matched} 发送者:{msg['sender']} 内容:{truncate_message(msg['content'])}")
                        sent = notifier.send(group_name, msg, matched)
                        cycle_stats["matches"] += len(matched)
                        cycle_stats["notifications"] += (1 if sent else 0)
                time.sleep(random.uniform(0.5, 1.5))  # 群间随机间隔
            except Exception as e:
                logging.error(f"处理群 {group_name} 时出错: {e}", exc_info=True)
        # 每10轮输出汇总指标
        cycle_elapsed = time.time() - cycle_start
        if cycle_count % 10 == 0:
            logging.info(
                f"[指标] 第{cycle_count}轮 | 总消息:{cycle_stats['messages_total']} "
                f"新增:{cycle_stats['messages_new']} 命中:{cycle_stats['matches']} "
                f"通知:{cycle_stats['notifications']} 耗时:{cycle_elapsed:.1f}s"
            )
        # 轮询间隔加入随机抖动(±10%)，避免固定周期被识别为机器人
        jitter = poll_interval * random.uniform(-0.1, 0.1)
        time.sleep(poll_interval + jitter)

if __name__ == '__main__':
    main()
```

### 5. 部署与运维方案

#### 5.1 服务器环境准备
- 安装Python 3.14.5，添加至PATH。
- 安装依赖包：`pip install pywinauto pywin32 pynput psutil`
- 安装QQ NT版，登录并勾选"记住密码"（QQ客户端保存密码后可在重启时自动登录，看门狗拉起QQ后依赖此功能）。首次启动需运维人员手动输入密码登录。
- 将需要监控的群窗口打开（可最小化到任务栏），将目标联系人窗口打开（或确保程序能通过搜索找到）。
- 电源选项：关闭显示器设为“从不”，禁用睡眠/休眠。
- 禁用锁屏：组策略 `计算机配置 → 管理模板 → 控制面板 → 个性化 → 不显示锁屏` 启用；或通过注册表。

#### 5.2 远程桌面保活配置
- 运行 `gpedit.msc` 定位到：`计算机配置 → 管理模板 → Windows组件 → 远程桌面服务 → 远程桌面会话主机 → 会话时间限制`
  - “设置断开会话的时间限制”：启用，设为“从不”
  - “达到时间限制时终止会话”：禁用
- 在服务器上执行 `powercfg -h off` 关闭休眠。

#### 5.3 自启动配置（不使用Windows服务）
- 编写 `start_monitor.bat`：
  ```batch
  @echo off
  cd /d D:\monitor
  python main.py >> logs\console.log 2>&1
  ```
- 将该批处理快捷方式放入 `shell:startup` 文件夹。
- 或使用任务计划程序：创建任务，触发器“登录时”，操作启动 `pythonw.exe` 带参数 `main.py`（但需设置“不管用户是否登录都要运行”为false，因为需要交互桌面）。

#### 5.4 看门狗脚本
创建 `watchdog.py`，独立运行，每30秒检查 `QQ.exe` 和 `main.py` 进程，若消失则启动。

```python
import psutil
import subprocess
import time
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def is_running(process_name):
    return any(p.info['name'] == process_name for p in psutil.process_iter(['name']))

def start_qq():
    qq_path = r"C:\Program Files\Tencent\QQ\QQ.exe"
    subprocess.Popen(qq_path)
    logging.warning("QQ.exe 已重启，将尝试自动登录（依赖QQ客户端保存的密码）")
    # 等待QQ启动并尝试自动登录
    time.sleep(30)
    # 检查QQ是否成功登录（主窗口出现且非登录界面）
    # 若30秒后仍未检测到已登录状态，记录ERROR提醒运维人员手动登录
    if not is_qq_logged_in():
        logging.error("QQ 已重启但未能自动登录，请运维人员手动登录QQ！")

def is_qq_logged_in():
    """通过检查QQ主窗口是否存在且不是登录窗口来判断登录状态"""
    import win32gui
    result = []
    def callback(hwnd, extra):
        title = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and "QQ" in title and "登录" not in title:
            extra.append(hwnd)
    win32gui.EnumWindows(callback, result)
    return len(result) > 0

def start_monitor():
    subprocess.Popen(["python", "main.py"], cwd=r"D:\monitor")

while True:
    if not is_running("QQ.exe"):
        start_qq()
    if not is_running("python.exe") or not any("main.py" in ' '.join(p.cmdline()) for p in psutil.process_iter(['cmdline']) if p.info['cmdline']):
        start_monitor()
    time.sleep(30)
```

将看门狗也加入自启动。

### 6. 配置文件示例 (`config.jsonc`)

配置文件使用 JSONC 格式（支持行内注释 `//` 和块注释 `/* */`），程序加载时自动去除注释后解析。

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
    // 同型号同群通知冷却时间（秒），默认30
    "cooldown_sec": 30,
    // 型号匹配是否区分大小写，默认false（不区分）
    "match_case_sensitive": false
}
```

### 7. 异常处理与恢复策略

| 异常场景 | 处理策略 |
|----------|----------|
| 群窗口找不到 | WARNING日志，跳过本次轮询，下次继续寻找（优先按群号匹配，降级按群名匹配） |
| 剪贴板复制失败 | 重试3次（每次尝试前清空剪贴板），递增退避（0.5s→1.0s→1.5s）；仍失败则跳过该群 |
| 发送消息失败（联系人窗口丢失） | 尝试通过主窗口搜索重新打开联系人窗口；失败则ERROR日志，放弃本次通知 |
| QQ进程崩溃 | 看门狗检测后重启QQ；QQ保存密码可自动登录；若30s内未检测到登录状态则记录ERROR并提醒运维人员 |
| QQ自动登录失败 | 看门狗记录ERROR日志提醒运维人员手动登录；主程序继续运行等待窗口恢复 |
| 程序主循环异常 | 捕获后记录ERROR，continue继续下一轮（不崩溃） |
| 产品CSV加载失败 | Fatal级别，程序退出（型号列表是关键依赖） |
| QQ连接失败（pywinauto UIA） | Fatal级别，程序退出（无法操作QQ） |

### 8. 测试与验证要点

- 手动在群中发送包含型号的消息，检查60秒内是否收到通知。
- 发送多条相同型号，确认30秒冷却期内不会重复通知。
- 最小化所有群窗口到任务栏，程序应能正常激活。
- 断开远程桌面后重连，检查程序是否继续运行（需正确配置组策略）。
- 长时间运行72小时，观察内存和CPU占用。

#### 8.1 演习模式（Dry-Run）

程序支持 `--dry-run` 命令行参数，用于部署前验证：

- 所有模块正常运行（窗口激活、消息采集、解析、匹配），但**不实际发送 QQ 通知**。
- 原本应发送的通知内容写入 `dry_run_verification.log` 文件（位于 `log_dir`），格式为：
  ```
  [DRY-RUN] 2026-05-27 14:30:12 | 群:电子元器件供需群 | 型号:174933-1 | 发送者:张三 | 原文前100字符:求购174933-1...
  ```
- 同时输出 INFO 日志标记当前处于演习模式。
- 演习模式下冷却计时仍正常运行（仅抑制实际发送），便于验证去重逻辑。

---

以上为修订后的需求文档及实现方案。可直接作为开发依据。