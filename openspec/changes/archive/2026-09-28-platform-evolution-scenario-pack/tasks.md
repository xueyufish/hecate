# Tasks

前置依赖已解除:`g1-mcp-action-enforcement`(#185)与 `g2-tool-receipt-recovery`(#186)已合并至 main。19/19 任务全部完成。

## 1. 场景包骨架与 manifest

- [x] 1.1 建立 `tests/scenarios/` 目录结构(manifest.yaml、corpus/、tools/、goldens/、baselines/),manifest 初始条目含场景 ID(`S<nn>`)、P01—P08 映射、入口类型、覆盖状态与未支持声明;验证:`python -c "import yaml; yaml.safe_load(open('tests/scenarios/manifest.yaml'))"` 通过且字段齐全
- [x] 1.2 编写 manifest 一致性测试:双向核对(有条目无测试、有测试无条目均失败)、P01—P08 每组有覆盖状态、未支持标注必须带原因;验证:一致性测试通过,临时制造一处漂移能使其失败

## 2. 合成语料与 stub 企业工具

- [x] 2.1 编写个位数合成文档与 `corpus.yaml`(每份含 version、acl、标准引用位置锚点);验证:语料清单可解析且属性齐全,文档内容与锚点对应
- [x] 2.2 实现 stub 测试工单服务(确定性记录调用、副作用与结果,支持注入成功/业务拒绝/故障);验证:其单元测试覆盖三种注入路径
- [x] 2.3 语料 ACL 断言:无权限主体检索受保护文档不可见、授权主体可见、引用可定位到 corpus.yaml 锚点;验证:对应测试通过

## 3. 可重复场景(REST 入口,无前置)

- [x] 3.1 S 场景"正常执行":Agent 经 HTTP 入口读取授权材料并产出符合预期 schema 的产物,断言业务结果不比文本;验证:场景测试通过并登记 manifest
- [x] 3.2 S 场景"拒绝动作":未授权写操作被拒且拒绝证据可查;验证:场景测试通过并登记 manifest
- [x] 3.3 S 场景"等待审批":高风险动作等待审批,批准后继续、拒绝不执行;验证:场景测试通过并登记 manifest
- [x] 3.4 S 场景"后端失联":确定性注入后端故障,状态显式未知/待对账,不虚报成功;验证:场景测试通过并登记 manifest

## 4. 旧路径黄金样本

- [x] 4.1 以 stub 模型经 HTTP 入口录制旧路径响应协议子集与事件类型序列至 `goldens/`,文件头记录录制时代码路径与用途;验证:结构断言测试通过
- [x] 4.2 负向验证 golden 断言边界:改变 stub 文本断言仍绿,删除协议字段断言变红;验证:两条临时检查均符合预期后还原

## 5. MCP 权限负例(前置:g1-mcp-action-enforcement 合并)

- [x] 5.1 S 场景"权限负例":viewer 写、跨租户访问、未批准写分别经 MCP 与 REST 入口提交,断言两者授权结果一致且均基于服务端可信上下文;验证:场景测试通过并登记 manifest

## 6. 副作用恢复与重复提交负例(前置:g2-tool-receipt-recovery 合并)

- [x] 6.1 S 场景"结果缺失":注入"动作已执行、结果未落盘"后触发恢复,断言受保护写入不重复执行且状态进入待对账;验证:场景测试通过并登记 manifest
- [x] 6.2 S 场景"恢复真实结果"与"参数变化冲突":恢复返回真实结果引用而非占位文本,同动作键参数变化被拒绝;验证:场景测试通过并登记 manifest
- [x] 6.3 S 场景"重复提交":同一动作重复提交仅产生一次受保护副作用;验证:场景测试通过并登记 manifest

## 7. Tier 2 记录基线(不进 CI)

- [x] 7.1 定义内容质量 rubric 与最小数据集,复用 `src/hecate/ops/evaluation/` 既有数据集/评估器格式并记录 evaluator 版本;验证:数据集经 evaluation 模块加载成功
- [x] 7.2 编写基线跑批脚本(无 `test_` 前缀,不入 CI 收集)并生成单 Agent 成本/结果基线 JSON 至 `baselines/`,文件头声明"记录,非门禁、业务收益未验证";验证:`pytest --collect-only -q tests/scenarios` 不收集该脚本,本地运行脚本能产出 JSON

## 8. 收尾核对

- [x] 8.1 manifest 最终覆盖核对:P01—P08 全组有覆盖状态,未支持项带原因;若 `platform-evolution-baseline` 基线文档 change 已合并,在其中补 manifest 引用,否则在本 PR 描述记录待同步项;验证:一致性测试通过,文档引用存在或待同步项已登记
- [x] 8.2 运行仓库验证四件套的受影响范围:`ruff check src/hecate/ tests/`、`ruff format --check src/ tests/`、`mypy src/`、`python -m pytest tests/scenarios tests/test_e2e -q`;验证:全部通过
