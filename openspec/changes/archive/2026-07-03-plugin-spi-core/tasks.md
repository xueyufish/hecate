## 1. 插件模块设置

- [x] 1.1 创建 `src/hecate/plugin/__init__.py`，包含公共导出（PluginManifest、PluginRegistry、PluginLifecycle）
- [x] 1.2 创建 `src/hecate/plugin/manifest.py`，包含 PluginManifest 数据类（冻结、type/name/version/api_version/min_platform_version/description/permissions）
- [x] 1.3 创建 `src/hecate/plugin/lifecycle.py`，包含 PluginLifecycle 协议（on_load、on_unload）
- [x] 1.4 创建 `src/hecate/plugin/registry.py`，包含 PluginRegistry 类（register/unregister/get_by_type/get_by_name/list_all，线程安全）

## 2. EvaluatorABC 实现

- [x] 2.1 创建 `src/hecate/plugin/spi/__init__.py`
- [x] 2.2 创建 `src/hecate/plugin/spi/evaluator.py`，包含 EvaluatorABC 抽象基类（name 属性、description 属性、evaluate 方法）
- [x] 2.3 重构 `src/hecate/services/evaluation/evaluator.py`：将 Evaluator 重命名为 BuiltinEvaluator，继承自 EvaluatorABC

## 3. 评估器注册

- [x] 3.1 更新 `src/hecate/services/evaluation/engine.py`，导入 PluginRegistry 并在启动时注册所有内置评估器
- [x] 3.2 验证所有 41 个评估器子类与 BuiltinEvaluator 基类兼容（无需修改子类）

## 4. 测试

- [x] 4.1 创建 `tests/test_plugin/test_manifest.py` — 测试 PluginManifest 创建、不可变性、相等性、哈希
- [x] 4.2 创建 `tests/test_plugin/test_registry.py` — 测试 PluginRegistry register/unregister/get/list、线程安全
- [x] 4.3 创建 `tests/test_plugin/test_lifecycle.py` — 测试 PluginLifecycle on_load/on_unload 钩子、异常处理
- [x] 4.4 创建 `tests/test_plugin/test_evaluator_abc.py` — 测试 EvaluatorABC 接口、BuiltinEvaluator 注册
- [x] 4.5 运行完整测试套件：`ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`
