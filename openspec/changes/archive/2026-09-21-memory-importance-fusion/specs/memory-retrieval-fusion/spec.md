# Spec Delta

## Purpose

定义事实记忆(L3 用户记忆与 L4 知识记忆)检索的信号采集与融合排序行为:以语义相关性为主导排序,叠加有界的时间衰减与重要度乘性偏置,并为每次检索提供分信号分解,使记忆检索质量可观测、可评估。

## ADDED Requirements

### Requirement: L3 语义相关性检索

L3 用户记忆的检索路径 SHALL 以语义相关性为主排序:在 scope 过滤后的候选集内,按查询向量与存储向量的 cosine 相似度排序。检索路径 SHALL NOT 仅按 `importance` 排序返回。

#### Scenario: 相关性优先于重要度

- **WHEN** 查询与某条 importance 较低的记忆语义高度相关,而某条 importance 较高的记忆与查询无关
- **THEN** 相关性高的记忆排在结果前列,而非 importance 最高者恒居首

#### Scenario: 无真实向量行不产生噪声

- **WHEN** 候选集中某行的 embedding 为遗留 mock 向量(未经真实向量化)
- **THEN** 该行不参与 cosine 相似度计算,以"无语义信号"身份按元数据排序兜底返回,并记录降级日志

### Requirement: L3 写入路径真实向量

L3 用户记忆的全部写入路径(API 创建、工具创建、整合 ADD)SHALL 存储真实 embedding;系统 SHALL 提供一次性回填以替换存量 mock 向量。未完成回填的行 SHALL 在检索时按"无语义信号"降级处理,SHALL NOT 被当作相似度证据。

#### Scenario: API 创建落真实向量

- **WHEN** 通过 REST API 创建一条 L3 记忆且 embedding 服务可用
- **THEN** 落库的向量为真实 embedding,后续语义检索可命中该条

#### Scenario: 写入时 embedding 服务不可用

- **WHEN** 创建时 embedding 服务不可用
- **THEN** 记忆照常落库并标记为待向量化,创建不被阻塞;检索时该行按无语义信号降级

### Requirement: 跨层统一量纲合并

`search_memories` 合并 L3 与 L4 命中时,SHALL 先将各层相关性归一到统一可比尺度再合并排序;SHALL NOT 对不同量纲的分数(如重要度与向量相似度)直接混合排序。

#### Scenario: L3 相关命中不被 L4 低相关命中压过

- **WHEN** 某查询在 L3 命中高度相关记忆、在 L4 仅命中低相关记忆
- **THEN** 合并结果按统一尺度排序,L3 相关命中排在 L4 低相关命中之前

### Requirement: 有界元数据偏置

系统 SHALL 提供"相关性 × 时间衰减 × 重要度乘数"的乘性偏置排序,且 SHALL 满足:

- `MEMORY_FUSION_BIAS_ENABLED` 缺省为 false;关闭时排序 SHALL 等价于纯相关性排序(各乘数恒为 1.0);
- 时间衰减锚点为 `last_confirmed_at`,SHALL 按层配置 half-life(缺省:L3 episodic 30 天,L3 semantic 180 天,L4 永不衰减);
- 重要度乘数 SHALL 以 0.5 为中性值(映射为 1.0×),线性映射并 clamp 到 [0.5×, 1.5×];
- 乘积 SHALL 设总下限(缺省 0.3×):偏置是排序修正,SHALL NOT 成为过滤器,SHALL NOT 将候选完全排除;
- 访问频率 SHALL NOT 进入查询时排序公式。

#### Scenario: 默认关闭时行为等价

- **WHEN** 使用缺省配置执行 `memory_search`
- **THEN** 排序仅由归一化相关性决定,衰减与重要度不改变结果顺序

#### Scenario: 开启后久未确认的记忆被降权

- **WHEN** bias 开启,两条相关性相同的 L3 episodic 记忆,一条 `last_confirmed_at` 在 30 天前、另一条在当天
- **THEN** 当天确认的记忆排序在前;30 天前的记忆分数被衰减乘数压低,但不低于乘积总下限

#### Scenario: 高重要度不能救回无关记忆

- **WHEN** bias 开启,某记忆 importance 为 1.0 但与查询相关性接近 0
- **THEN** 该记忆仍排在相关性结果之后(乘性偏置不抬升无关内容的位次)

### Requirement: 访问信号采集

检索命中 SHALL 维护两类访问计数:既有 `access_count`(累计检索次数,语义不变)与按唯一会话去重的伴生计数;任何以访问热度为输入的评分 SHALL 使用去重计数而非累计次数。

#### Scenario: 重复检索不刷高热度

- **WHEN** 同一会话对相似查询反复执行 `memory_search` 并命中同一记忆
- **THEN** `access_count` 随每次命中递增,唯一会话计数保持不变

#### Scenario: 检索不刷新确认锚点

- **WHEN** 某记忆被检索命中
- **THEN** 其 `last_confirmed_at` 保持不变

### Requirement: 检索分信号可观测

检索结果 SHALL 携带分信号分解(相关性分、衰减乘数、重要度乘数);分解 SHALL 在偏置开关两种状态下均可用(关闭时乘数为 1.0)。

#### Scenario: breakdown 可查询

- **WHEN** 执行一次 `memory_search`
- **THEN** 每条结果的元数据包含相关性分与各乘数,可据此解释排序结果

### Requirement: 信号独立降级

任一信号源故障(查询向量化失败、embedding 服务不可用等)SHALL 独立降级:记录 warn 日志并回退到可用的排序(按重要度/时间),SHALL NOT 使检索调用失败;记忆子系统故障 SHALL NOT 阻塞在线会话。

#### Scenario: 查询向量化失败降级

- **WHEN** 查询向量化调用超时或失败
- **THEN** 本次检索返回降级排序结果并记录 warn,调用方不收到错误
