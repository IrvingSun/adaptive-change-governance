# Adaptive Change Governance — MVP Specification

## 1. 项目名称

**Adaptive Change Governance**

一个面向 AI Coding Agent 的“变更治理与流程路由系统”。

系统不直接从用户需求进入技术方案或代码修改，而是先结合：

- 用户诉求；
- 当前版本代码事实；
- 项目固有风险画像；
- 组织风险围栏；
- 测试、依赖和可回滚性信息；

生成本次变更的：

1. 风险评估；
2. 流程方案；
3. 必须执行的质量门禁；
4. 可跳过环节；
5. 流程升级条件。

流程方案确认后，系统再基于该流程方案生成技术方案，并进入实现。

---

# 2. 背景

当前多数 AI Coding Workflow 存在以下问题：

1. 用户输入需求后，Agent 直接进入技术实现；
2. 不同类型的任务被套用同一套固定流程；
3. 流程强度主要依据任务描述和代码量判断；
4. 无法识别“小改动、高风险”的场景；
5. 流程方案和技术方案混杂；
6. 执行中发现新风险后，无法动态升级流程；
7. 风险控制仍依赖程序员或技术负责人脑中的隐性经验。

本项目希望把这些隐性判断转化为：

- 可读取；
- 可配置；
- 可解释；
- 可审计；
- 可被 AI 执行；

的工程治理机制。

---

# 3. 核心原则

## 3.1 需求描述不等于变更事实

用户描述只代表诉求。

最终判断必须结合：

- 当前代码；
- 当前分支；
- 依赖关系；
- 数据库变更；
- 消息结构；
- 测试覆盖；
- 部署方式；
- 项目风险画像。

---

## 3.2 流程方案和技术方案必须分离

### 流程方案回答

- 这件事应该以什么规格处理；
- 哪些分析必须做；
- 哪些门禁不能跳；
- 是否需要人工审批；
- 是否需要灰度、回滚、对账；
- 哪些步骤可以省略。

### 技术方案回答

- 修改哪些模块；
- 如何实现；
- 如何测试；
- 如何发布；
- 如何回滚。

Agent 必须先生成流程方案，再生成技术方案。

---

## 3.3 风险围栏优先于模型判断

模型可以：

- 补充判断；
- 分析上下文；
- 调整流程深度；
- 推荐流程模块。

模型不能取消硬性围栏。

例如：

- 涉及金额计算，必须有独立审查；
- 破坏性 SQL，必须有 dry-run 和人工确认；
- 设备控制指令，必须有协议核对和真机验证。

---

## 3.4 流程是模块组合，不是固定套餐

系统不应只在 L1、L2、L3、L4 中选择一个完整模板。

系统应根据任务实际情况组合流程模块。

例如：

- 代码事实扫描；
- 调用链分析；
- 数据影响分析；
- 技术方案；
- 回归测试；
- 独立审查；
- 对抗审查；
- 灰度发布；
- 上线观察；
- 数据对账。

---

## 3.5 流程允许动态升级

初始判断不是最终判断。

执行中出现以下情况时，系统必须重新评估：

- 发现跨服务影响；
- 发现历史数据污染；
- 无法安全回滚；
- 测试不足；
- 依赖范围扩大；
- 生产影响高于预期；
- 出现安全、资金、权限、隐私或设备控制风险。

---

# 4. MVP目标

第一版不追求全自动研发治理平台。

MVP 只实现以下闭环：

```text
用户输入需求
→ 读取项目风险画像
→ 扫描当前代码
→ 生成代码事实包
→ 应用风险围栏
→ 生成流程方案
→ 人工确认
→ 生成技术方案
→ 人工确认
→ 执行实现
→ 实现后重新评估
→ 输出验证报告
```

---

# 5. 非目标

第一版不实现：

- 自动部署；
- 自动灰度；
- 自动回滚；
- 完整 CI/CD 集成；
- Web 管理后台；
- 多团队权限体系；
- 自动学习历史事故；
- 复杂机器学习评分模型；
- 自动修改组织规则；
- 完整依赖图数据库；
- 多 Agent 调度平台。

MVP 以 Codex CLI 本地执行为主。

---

# 6. 使用方式

提供统一命令：

```bash
/change-assess <需求描述>
```

可选参数：

```bash
/change-assess "<需求描述>" \
  --mode assess|design|execute|reassess \
  --risk-profile .ai-governance/project-risk.yaml \
  --guardrails .ai-governance/guardrails.yaml \
  --output .ai-governance/runs
```

默认模式：

```text
assess
```

---

# 7. 目录结构

```text
.ai-governance/
├── project-risk.yaml
├── guardrails.yaml
├── workflow-modules.yaml
├── assessment-schema.yaml
├── artifact-schemas.yaml
├── risk-calibration.yaml
├── risk-scenarios.yaml
├── profiles/
│   └── charging-platform/
│       ├── project-risk.yaml
│       └── guardrails.yaml
├── templates/
│   ├── evidence-pack.md
│   ├── workflow-plan.md
│   ├── technical-plan.md
│   ├── verification-report.md
│   └── human-review.md
└── runs/
    └── <timestamp>-<slug>/          # 每次评估一个隔离 run，默认 gitignored
        ├── request.md
        ├── evidence-pack.yaml
        ├── risk-assessment.yaml / .md
        ├── investigation-questions.yaml / .md
        ├── workflow-recommendation.yaml
        ├── workflow-plan.md
        ├── review.md
        ├── human-review.yaml
        ├── progress.yaml
        ├── run-state.yaml
        ├── analysis-report.yaml / .md          # analysis_only / decision_support
        ├── approved-workflow.yaml / -plan.md    # 工作流批准后
        ├── agent-tasks.yaml / .md               # L3/L4 拆分子任务
        ├── technical-plan.yaml / .md
        ├── approved-technical-plan.yaml / .md   # 技术方案批准后
        ├── diff-verification.yaml / .md         # 实现后
        ├── reassessment.yaml / .md              # 复评
        └── verification-report.yaml / .md       # 最终验证
```

---

# 8. 项目风险画像

文件：

```text
.ai-governance/project-risk.yaml
```

示例：

```yaml
version: 1

project:
  name: charging-platform
  description: 充电业务核心平台
  baseline_level: L4

business_risk:
  criticality: 5
  money_sensitivity: 5
  data_integrity: 5
  device_control: 5
  privacy: 3
  compliance: 3
  customer_impact: 5

engineering_health:
  automated_test_coverage: 2
  observability: 3
  rollback_capability: 2
  architecture_clarity: 2
  dependency_complexity: 4
  documentation_quality: 2

critical_domains:
  - billing
  - refund
  - order-state
  - device-command
  - authentication
  - authorization
  - settlement
  - reconciliation

critical_paths:
  - order creation
  - charging start
  - charging stop
  - billing
  - refund
  - settlement

known_constraints:
  - production database cannot be modified directly by agent
  - destructive SQL requires manual approval
  - device command changes require real device validation
  - money-related changes require reconciliation

default_human_gates:
  - workflow_plan_approval
  - technical_plan_approval
  - production_change_approval
```

---

# 9. 风险围栏

文件：

```text
.ai-governance/guardrails.yaml
```

示例：

```yaml
version: 1

hard_guardrails:

  - id: money-change
    when:
      any:
        - affected_domain: money
        - affected_domain: billing
        - affected_domain: refund
        - affected_domain: settlement
        - affected_domain: reconciliation
    require:
      - business_rule_confirmation
      - boundary_test
      - independent_review
      - reconciliation_test
      - rollback_or_compensation_plan
      - post_release_monitoring

  - id: destructive-database-operation
    when:
      any:
        - operation: delete
        - operation: truncate
        - operation: irreversible_migration
        - operation: bulk_update
    require:
      - dry_run
      - affected_row_estimation
      - backup_or_restore_plan
      - manual_approval
    prohibit:
      - direct_production_execution

  - id: device-control
    when:
      any:
        - affected_domain: device-command
        - affected_domain: charging-control
        - affected_domain: firmware
    require:
      - protocol_verification
      - failure_mode_analysis
      - real_device_test
      - rollback_plan

  - id: auth-security
    when:
      any:
        - affected_domain: authentication
        - affected_domain: authorization
        - affected_domain: credential
        - affected_domain: token
    require:
      - threat_analysis
      - negative_test
      - independent_review
      - security_regression_test

  - id: public-interface-change
    when:
      any:
        - change_type: public_api
        - change_type: message_schema
        - change_type: database_schema
    require:
      - compatibility_analysis
      - consumer_analysis
      - migration_plan
```

规则优先级：

```text
hard_guardrails > AI建议 > 默认流程
```

---

# 10. 流程模块定义

文件：

```text
.ai-governance/workflow-modules.yaml
```

示例：

```yaml
version: 1

modules:

  requirement_confirmation:
    description: 明确业务目标、边界和验收标准
    output: confirmed-requirement.md

  code_fact_scan:
    description: 扫描当前代码，识别相关文件、模块和依赖
    output: evidence-pack.yaml

  dependency_analysis:
    description: 分析调用链、事件、消息、数据库和外部依赖
    output: dependency-analysis.md

  data_impact_analysis:
    description: 分析历史数据、数据迁移和数据一致性影响
    output: data-impact.md

  business_rule_confirmation:
    description: 核实业务规则及边界条件
    output: business-rule.md

  technical_design:
    description: 形成具体技术方案
    output: technical-plan.md

  test_design:
    description: 形成测试范围和验证策略
    output: test-plan.md

  boundary_test:
    description: 验证边界、异常和极端输入
    output: test-report.md

  regression_test:
    description: 验证相关模块无回归
    output: regression-report.md

  reconciliation_test:
    description: 对关键业务结果进行对账
    output: reconciliation-report.md

  independent_review:
    description: 由独立上下文或独立模型进行审查
    output: review-report.md

  adversarial_review:
    description: 针对遗漏、风险和错误假设进行对抗审查
    output: adversarial-review.md

  threat_analysis:
    description: 分析权限、安全和攻击面
    output: threat-analysis.md

  compatibility_analysis:
    description: 验证上下游兼容性
    output: compatibility-report.md

  real_device_test:
    description: 使用真实设备验证
    output: device-test-report.md

  staged_release:
    description: 分阶段发布
    output: rollout-plan.md

  rollback_plan:
    description: 明确回滚路径
    output: rollback-plan.md

  rollback_or_compensation_plan:
    description: 明确回滚或业务补偿机制
    output: rollback-plan.md

  post_release_monitoring:
    description: 定义上线观察指标和时长
    output: monitoring-plan.md

  incident_review:
    description: 重大变更或事故后复盘
    output: incident-review.md
```

---

# 11. 变更事实包

Agent 必须先生成变更事实包，再进行风险评分。

文件：

```text
evidence-pack.yaml
```

结构：

```yaml
version: 1

request:
  original: 用户原始需求
  normalized_intent: 归一化后的目标
  acceptance_criteria:
    - 验收条件

repository:
  branch: current-branch
  commit: current-commit
  dirty: false

code_findings:
  direct_files:
    - path: src/...
      reason: 直接实现位置

  related_files:
    - path: src/...
      reason: 调用或依赖关系

  affected_modules:
    - order-service
    - billing-service

  affected_domains:
    - billing
    - refund

  change_types:
    - business_logic
    - public_api

  database_changes: false
  message_schema_changes: false
  public_api_changes: true
  scheduled_jobs_affected: false
  configuration_changes: false

dependency_findings:
  upstream:
    - module-a

  downstream:
    - module-b

  external_dependencies:
    - payment-provider

test_findings:
  existing_tests:
    - test/path

  coverage_confidence: low
  missing_test_areas:
    - refund boundary cases

runtime_findings:
  production_usage: unknown
  traffic_level: unknown
  observability: medium
  rollback_capability: low

unknowns:
  - 是否影响历史订单
  - 是否存在旧版本客户端兼容问题

evidence_sources:
  - user_request
  - code_search
  - git_diff
  - test_files
  - project_risk_profile
  - guardrails
```

---

# 12. 风险评估模型

MVP 使用规则加权，不使用机器学习。

## 12.1 评分维度

每项 1 到 5 分：

```yaml
risk_dimensions:
  business_criticality: 1-5
  production_impact: 1-5
  change_scope: 1-5
  dependency_coupling: 1-5
  uncertainty: 1-5
  reversibility: 1-5
  data_risk: 1-5
  security_risk: 1-5
  testability_risk: 1-5
  observability_risk: 1-5
```

注意：

- reversibility 分数越高，表示越难回滚；
- testability_risk 越高，表示越难验证；
- observability_risk 越高，表示越难发现异常。

---

## 12.2 基础权重

```yaml
weights:
  business_criticality: 1.3
  production_impact: 1.4
  change_scope: 0.8
  dependency_coupling: 1.0
  uncertainty: 1.1
  reversibility: 1.2
  data_risk: 1.3
  security_risk: 1.3
  testability_risk: 1.0
  observability_risk: 0.9
```

总分：

```text
weighted_score =
Σ(dimension_score × weight)
```

---

## 12.3 风险等级

```yaml
levels:
  L1:
    max_score: 15

  L2:
    min_score: 15
    max_score: 27

  L3:
    min_score: 27
    max_score: 40

  L4:
    min_score: 40
```

硬围栏可以直接提升最低等级。

示例：

```yaml
level_overrides:
  money-change:
    minimum_level: L3

  destructive-database-operation:
    minimum_level: L4

  auth-security:
    minimum_level: L3

  device-control:
    minimum_level: L3
```

---

# 13. 流程方案输出

文件：

```text
workflow-plan.md
```

必须包含：

```markdown
# Workflow Plan

## 1. 任务摘要

## 2. 当前代码事实

## 3. 风险评估

- 项目基线等级
- 本次任务评分
- 最终建议等级
- 命中的硬围栏

## 4. 建议流程

按执行顺序列出。

## 5. 必须执行的流程模块

## 6. 可选模块

## 7. 明确跳过的模块

必须说明跳过原因。

## 8. 人工确认节点

## 9. 流程升级条件

## 10. 未知信息

## 11. 判断依据

必须引用代码事实、项目画像或风险规则。
```

示例：

```yaml
workflow_recommendation:
  baseline_level: L4
  calculated_level: L2
  final_level: L3

  triggered_guardrails:
    - public-interface-change

  required_modules:
    - requirement_confirmation
    - code_fact_scan
    - dependency_analysis
    - compatibility_analysis
    - technical_design
    - test_design
    - regression_test
    - independent_review

  optional_modules:
    - staged_release

  skipped_modules:
    - threat_analysis
    - real_device_test
    - reconciliation_test

  human_gates:
    - workflow_plan_approval
    - technical_plan_approval

  escalation_triggers:
    - discovers_database_schema_change
    - discovers_historical_data_impact
    - no_safe_rollback
```

---

# 14. 技术方案生成规则

只有以下条件满足后，才可以生成技术方案：

```text
workflow-plan.md 已生成
AND
workflow plan 已人工确认
```

技术方案必须覆盖流程方案中所有 required_modules。

文件：

```text
technical-plan.md
```

结构：

```markdown
# Technical Plan

## 1. 目标

## 2. 非目标

## 3. 当前实现

## 4. 变更范围

## 5. 修改方案

## 6. 数据与兼容性

## 7. 测试方案

## 8. 发布方案

## 9. 回滚或补偿方案

## 10. 上线观察

## 11. 风险与未决项

## 12. 与流程方案的对应关系
```

最后一节必须展示：

```text
流程模块 → 技术方案章节 → 实际产物
```

---

# 15. 执行状态机

```text
NEW
  ↓
EVIDENCE_COLLECTED
  ↓
WORKFLOW_PROPOSED
  ↓
WORKFLOW_APPROVED
  ↓
TECHNICAL_PLAN_PROPOSED
  ↓
TECHNICAL_PLAN_APPROVED
  ↓
IMPLEMENTING
  ↓
REASSESSING
  ↓
VERIFYING
  ↓
COMPLETED
```

异常状态：

```text
BLOCKED
NEEDS_CLARIFICATION
WORKFLOW_UPGRADED
FAILED_VERIFICATION
CANCELLED
```

---

# 16. 动态再评估

以下时机必须触发 reassess：

1. 技术方案生成前；
2. 实现完成后；
3. 新增或删除关键文件时；
4. 发现跨模块依赖时；
5. 发现数据库、消息或公共接口变化时；
6. 测试失败且原因不明确时；
7. 无法提供安全回滚方案时；
8. 发现历史数据影响时；
9. 实际变更范围大于初始预估时。

重新评估输出：

```yaml
reassessment:
  previous_level: L2
  new_level: L3

  reasons:
    - discovered_cross_service_dependency
    - no_existing_regression_tests

  added_modules:
    - dependency_analysis
    - adversarial_review
    - staged_release

  removed_modules: []

  requires_human_reapproval: true
```

---

# 17. Codex / Claude 命令行为

两个宿主共用同一套 `change-assess` CLI 运行时，入口不同：

```text
Claude Code：commands/change-assess.md（slash 命令）+ skills/change-governance/SKILL.md
Codex CLI：  skills/change-governance/SKILL.md
```

Codex 插件模型只声明 `skills`、不声明 command，因此 Codex 端的门禁纪律由 skill 自我强制（无 PreToolUse hook）。

命令逻辑（两端一致）：

```text
阶段一：解析参数

阶段二：读取项目画像和风险围栏

阶段三：检查当前仓库状态
- 当前分支
- 当前 commit
- git status
- 是否存在未提交修改

阶段四：分析用户诉求
- 归一化目标
- 识别验收条件
- 识别关键词和风险域

阶段五：扫描代码
- 搜索相关符号
- 搜索相关接口
- 搜索数据表和消息
- 搜索测试
- 搜索调用方和被调用方
- 检查当前 diff

阶段六：生成 evidence-pack.yaml

阶段七：应用硬围栏

阶段八：执行风险评分

阶段九：生成 workflow-plan.md

阶段十：停止，等待人工确认

阶段十一：确认后生成 technical-plan.md

阶段十二：再次停止，等待人工确认

阶段十三：确认后执行实现

阶段十四：实现后重新评估

阶段十五：执行验证并生成 verification-report.md
```

---

# 18. 人工确认协议

MVP 不做复杂交互系统。

通过状态文件确认：

```text
.workflow-approved
.technical-plan-approved
```

或通过命令参数：

```bash
/change-assess --approve-workflow <run_id>
/change-assess --approve-plan <run_id>
/change-assess --execute <run_id>
```

未确认时禁止进入下一阶段。

---

# 19. 安全限制

Codex CLI 在 MVP 中不得：

- 直接执行生产 SQL；
- 修改生产配置；
- 自动部署；
- 自动操作云资源；
- 自动发送外部通知；
- 自动删除数据；
- 自动执行不可逆迁移；
- 绕过人工确认；
- 自动降低硬围栏等级。

如需要执行敏感命令，只能输出建议命令和风险说明。

---

# 20. 输出要求

所有判断必须区分：

```text
FACT
INFERENCE
UNKNOWN
DECISION
```

示例：

```markdown
- FACT：RefundService 被 OrderService 和 SettlementJob 调用。
- FACT：当前未发现退款边界测试。
- INFERENCE：修改可能影响结算一致性。
- UNKNOWN：历史订单是否需要重新计算。
- DECISION：本次流程至少提升至 L3。
```

禁止将推断写成事实。

---

# 21. MVP实现建议

建议分成三个独立组件。

## 21.1 Repository Analyzer

职责：

- Git 状态；
- 文件搜索；
- 关键符号搜索；
- 依赖粗分析；
- 测试发现；
- 当前 diff 分析。

输出：

```text
evidence-pack.yaml
```

---

## 21.2 Risk Evaluator

职责：

- 读取项目画像；
- 读取围栏；
- 根据事实包评分；
- 命中硬规则；
- 给出最终风险级别。

输出：

```text
risk-assessment.yaml
```

---

## 21.3 Workflow Composer

职责：

- 根据风险等级；
- 硬围栏；
- 未知信息；
- 代码事实；

组合流程模块。

输出：

```text
workflow-plan.md
```

---

# 22. 建议实现顺序

## Phase 1：静态配置

实现：

- project-risk.yaml
- guardrails.yaml
- workflow-modules.yaml
- schema 校验

验收：

- 配置文件可解析；
- 错误配置可明确报错。

---

## Phase 2：代码事实扫描

实现：

- Git 信息；
- 关键词搜索；
- 相关文件搜索；
- 测试文件发现；
- 当前 diff；
- 粗粒度依赖关系。

验收：

- 能生成完整 evidence-pack.yaml；
- 每条事实包含证据来源。

---

## Phase 3：风险评分

实现：

- 权重评分；
- 硬围栏；
- 最低等级覆盖；
- 未知项惩罚。

验收：

- 相同输入产生稳定结果；
- 命中硬围栏时不能降级。

---

## Phase 4：流程生成

实现：

- 流程模块组合；
- 必选、可选、跳过；
- 人工门禁；
- 升级条件。

验收：

- 流程方案能说明原因；
- 流程方案不包含具体代码实现。

---

## Phase 5：技术方案关联

实现：

- 技术方案模板；
- 流程模块到方案章节的映射；
- 未确认流程时禁止生成技术方案。

验收：

- 技术方案覆盖所有必选流程模块。

---

## Phase 6：动态再评估

实现：

- 基于 diff 重新扫描；
- 对比初始事实包；
- 自动检测风险变化；
- 生成 reassessment。

验收：

- 发现跨服务、数据库、公共接口变化时自动升级。

---

# 23. 验收用例

## 用例一：低风险文案修改

输入：

```text
修改后台订单页面上的提示文案。
```

预期：

```yaml
final_level: L1
required_modules:
  - code_fact_scan
  - regression_test
skipped_modules:
  - technical_design
  - staged_release
  - rollback_plan
```

---

## 用例二：退款金额计算修改

输入：

```text
调整退款金额保留两位小数的计算方式。
```

预期：

```yaml
final_level: L3
triggered_guardrails:
  - money-change
required_modules:
  - business_rule_confirmation
  - boundary_test
  - independent_review
  - reconciliation_test
  - rollback_or_compensation_plan
  - post_release_monitoring
```

---

## 用例三：删除历史数据

输入：

```text
删除重复的设备端口状态数据。
```

预期：

```yaml
final_level: L4
triggered_guardrails:
  - destructive-database-operation
required_modules:
  - dry_run
  - affected_row_estimation
  - backup_or_restore_plan
  - manual_approval
prohibited:
  - direct_production_execution
```

---

## 用例四：设备断电命令调整

输入：

```text
修改设备停止充电指令的重试逻辑。
```

预期：

```yaml
final_level: L3
triggered_guardrails:
  - device-control
required_modules:
  - protocol_verification
  - failure_mode_analysis
  - real_device_test
  - rollback_plan
```

---

## 用例五：初始低风险，执行中升级

初始输入：

```text
修复一个订单状态展示问题。
```

初始判断：

```yaml
final_level: L2
```

代码扫描后发现：

- 公共状态枚举；
- 多服务消费；
- 历史数据依赖；
- 无自动化测试。

预期再评估：

```yaml
new_level: L3
added_modules:
  - compatibility_analysis
  - dependency_analysis
  - adversarial_review
  - staged_release
```

---

# 24. 成功标准

MVP 成功不是看它生成了多少文档，而是看以下指标：

1. 是否能识别小改动中的高风险；
2. 是否能避免低风险任务走重流程；
3. 是否能引用代码事实解释流程判断；
4. 是否能稳定执行硬围栏；
5. 是否能区分流程方案和技术方案；
6. 是否能在实现中发现新风险并升级流程；
7. 是否能让技术负责人快速审阅和覆盖建议；
8. 是否比固定流程更合理；
9. 是否比纯人工判断更稳定；
10. 是否减少 Agent 直接进入实现造成的返工。

---

# 25. 第一版完成定义

满足以下条件即认为 MVP 完成：

- 存在统一 `/change-assess` 命令；
- 能读取项目画像；
- 能读取硬风险围栏；
- 能分析当前仓库；
- 能生成变更事实包；
- 能进行风险评分；
- 能生成流程方案；
- 流程方案需要人工确认；
- 能根据已确认流程生成技术方案；
- 技术方案需要人工确认；
- 实现后能重新评估；
- 能输出验证报告；
- 全流程产物保存在独立 run 目录；
- 所有重要判断可追溯到事实或规则。

---

# 26. 后续演进方向

MVP 完成后可以继续扩展：

- 历史事故模式匹配；
- GitHub PR 集成；
- CI 质量门禁；
- 变更风险趋势；
- 团队级项目画像；
- 审批角色配置；
- 自动关联监控指标；
- 自动生成灰度策略；
- 多模型独立评估；
- 流程建议效果回溯；
- 基于历史结果优化评分权重；
- 组织级 Governance Harness。
