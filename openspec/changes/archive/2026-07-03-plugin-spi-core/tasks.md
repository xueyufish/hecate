## 1. Plugin Module Setup

- [x] 1.1 Create `src/hecate/plugin/__init__.py` with public exports (PluginManifest, PluginRegistry, PluginLifecycle)
- [x] 1.2 Create `src/hecate/plugin/manifest.py` with PluginManifest dataclass (frozen, type/name/version/api_version/min_platform_version/description/permissions)
- [x] 1.3 Create `src/hecate/plugin/lifecycle.py` with PluginLifecycle Protocol (on_load, on_unload)
- [x] 1.4 Create `src/hecate/plugin/registry.py` with PluginRegistry class (register/unregister/get_by_type/get_by_name/list_all, thread-safe)

## 2. EvaluatorABC Implementation

- [x] 2.1 Create `src/hecate/plugin/spi/__init__.py`
- [x] 2.2 Create `src/hecate/plugin/spi/evaluator.py` with EvaluatorABC abstract base class (name property, description property, evaluate method)
- [x] 2.3 Refactor `src/hecate/services/evaluation/evaluator.py`: rename Evaluator to BuiltinEvaluator, inherit from EvaluatorABC

## 3. Evaluator Registration

- [x] 3.1 Update `src/hecate/services/evaluation/engine.py` to import PluginRegistry and register all built-in evaluators at startup
- [x] 3.2 Verify all 41 evaluator subclasses work with BuiltinEvaluator base class (no changes needed to subclasses)

## 4. Tests

- [x] 4.1 Create `tests/test_plugin/test_manifest.py` — test PluginManifest creation, immutability, equality, hashing
- [x] 4.2 Create `tests/test_plugin/test_registry.py` — test PluginRegistry register/unregister/get/list, thread safety
- [x] 4.3 Create `tests/test_plugin/test_lifecycle.py` — test PluginLifecycle on_load/on_unload hooks, exception handling
- [x] 4.4 Create `tests/test_plugin/test_evaluator_abc.py` — test EvaluatorABC interface, BuiltinEvaluator registration
- [x] 4.5 Run full test suite: `ruff check src/hecate/ tests/ && ruff format --check src/ tests/ && mypy src/ && python -m pytest tests/ -q`
