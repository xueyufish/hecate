## 1. Event Types — 事件类型

- [x] 1.1 创建 `src/hecate/engine/eventstore.py`，包含 `EventType` 字符串枚举（NODE_START、NODE_END、TOOL_CALL、TOOL_RESULT、CHANNEL_WRITE、LLM_REQUEST、LLM_RESPONSE、INTERRUPT、RESUME、ERROR、CUSTOM）
- [x] 1.2 定义冻结的 `Event` 数据类，包含字段：id（UUID，default_factory）、session_id（UUID）、superstep（int）、event_type（EventType）、node_id（str | None）、timestamp（datetime，default_factory utcnow）、payload（dict，default_factory）、version（int = 0）
- [x] 1.3 验证文件顶部有 `from __future__ import annotations`，所有公开符号都有文档字符串

## 2. EventStore ABC — EventStore ABC

- [x] 2.1 在 `eventstore.py` 中定义 `EventStore(ABC)`，包含抽象方法：`append(event: Event) -> UUID`、`get_events(session_id, from_version=0) -> list[Event]`、`replay(session_id, from_version=0) -> AsyncGenerator[Event, None]`、`get_version(session_id) -> int`
- [x] 2.2 为 EventStore ABC 和每个抽象方法添加完整的文档字符串（英文，匹配现有 EnginePort/CheckpointStore 风格）

## 3. InMemoryEventStore — InMemoryEventStore 实现

- [x] 3.1 使用 `dict[UUID, list[Event]]` 内部存储实现 `InMemoryEventStore(EventStore)`
- [x] 3.2 `append` 为每个会话分配顺序版本号（从 1 开始），追加到列表中，返回 event.id
- [x] 3.3 `get_events` 按 session_id 和 from_version 过滤，以列表形式返回匹配的事件
- [x] 3.4 `replay` 通过异步生成器产出事件（在过滤后的列表上异步迭代）
- [x] 3.5 `get_version` 返回会话的最高版本号，若无事件则返回 0
- [x] 3.6 验证 InMemoryEventStore 处理边界情况：空会话、不存在的会话、from_version 超出范围

## 4. EnginePort Integration — EnginePort 集成

- [x] 4.1 在 `ports.py` 的 `EnginePort` 中添加 `event_store: EventStore | None = None` 属性（可选，默认 None）
- [x] 4.2 验证 `ports.py` 使用 `TYPE_CHECKING` 防护从 `hecate.engine.eventstore` 导入 `EventStore` 和 `Event`（避免循环导入）

## 5. Tests — 测试

- [x] 5.1 创建 `tests/test_engine/test_eventstore.py`
- [x] 5.2 测试 EventType 枚举值是正确的字符串
- [x] 5.3 测试带有自动生成 id 和 timestamp 的 Event 创建
- [x] 5.4 测试 Event 不可变性（设置字段引发 FrozenInstanceError）
- [x] 5.5 测试 EventStore 是抽象的（不能直接实例化）
- [x] 5.6 测试 InMemoryEventStore.append 返回 UUID 并按顺序分配版本号
- [x] 5.7 测试 InMemoryEventStore.get_events 按会话过滤并按版本号筛选
- [x] 5.8 测试 InMemoryEventStore.replay 通过异步生成器产出事件
- [x] 5.9 测试 InMemoryEventStore.get_version 返回正确版本号
- [x] 5.10 测试 EnginePort.event_store 默认为 None
- [x] 5.11 测试 EnginePort 可以设置 event_store

## 6. Verification — 验证

- [x] 6.1 运行 `ruff check src/hecate/engine/eventstore.py src/hecate/engine/ports.py tests/test_engine/test_eventstore.py`
- [x] 6.2 运行 `ruff format --check src/hecate/engine/eventstore.py src/hecate/engine/ports.py tests/test_engine/test_eventstore.py`
- [x] 6.3 运行 `mypy src/hecate/engine/eventstore.py src/hecate/engine/ports.py`
- [x] 6.4 运行 `python -m pytest tests/test_engine/test_eventstore.py -v`
- [x] 6.5 运行完整测试套件 `python -m pytest tests/ -q` — 无回归