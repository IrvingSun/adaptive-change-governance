# Adaptive Change Governance

给 AI 编码 agent 用的**变更治理与工作流路由层**。

---

## 一、这是什么

一句话：**在 AI agent 动手改代码之前，先把「这次改动有多大风险」判出来，按风险决定要走多重的流程，并在关键节点卡一道人工确认，全程留审计痕迹。**

它是三样东西：

| 是 | 不是 |
|---|---|
| 流程治理（该走的步骤不许跳） | 安全沙箱 |
| 审计留痕（谁在什么风险下批准了什么） | 权限系统 |
| PR 合并前的风险复核 | 能物理阻止 agent 的东西 |

**说清楚边界**：它降低「agent 埋头把高风险改动做完、没人看过」的概率；它**不能**让这件事在物理上不可能发生。详见 [第六节：它到底能守住多少](#六它到底能守住多少)。

### 解决什么问题

AI agent 改代码的典型失败不是「代码写错了」，而是**流程失控**：

- 一句「把菜单文案改一下」和一句「把过期订单清理掉」，agent 用**同样的力度**对待——前者小题大做，后者草率执行。
- agent 自己判断「这个改动很简单」，然后直接动手，没人有机会介入。
- 出问题后复盘，**没有任何记录**说明当时基于什么判断、谁同意的。

这个插件把这三件事变成有规则、可配置、可审计的流程。

---

## 二、核心机制

### 1. 风险分级 L1–L4

十个维度加权打分，超过阈值升级：

| 维度 | 权重 | 维度 | 权重 |
|---|---|---|---|
| `production_impact` | 1.4 | `data_risk` | 1.3 |
| `business_criticality` | 1.3 | `security_risk` | 1.3 |
| `reversibility` | 1.2 | `uncertainty` | 1.1 |
| `dependency_coupling` | 1.0 | `testability_risk` | 1.0 |
| `observability_risk` | 0.9 | `change_scope` | 0.8 |

阈值在 `.ai-governance/risk-calibration.yaml`：`L2: 15`、`L3: 27`、`L4: 40`。

**关键设计：需求文本只用来定位代码，不用来打分。**

这是这个插件最重要的一个取舍。「帮我清理下过期数据」听起来吓人，可能只是删几行注释；「调整一个字段默认值」听起来无害，可能是结算逻辑。**字面意思不可靠**，所以文本只负责回答「该去看哪些文件」，风险由三个来源决定：

- **代码事实**（`code_signals.py`）——改动的代码里实际出现了什么。路由定义、鉴权守卫、设备协议调用、消息发布、金额算术，各自映射到对应领域。
- **影响面 / 引用扇出**（`reference_scanner.py`）——改的这个符号，**有多少地方在引用它**。这是最朴素也最硬的信号：改一个 200 处引用的函数，风险天然就高。计分梯度：
  ```
  ≥200 引用 或 ≥5 模块 → 5    ≥50 或 ≥3 模块 → 4
  ≥10 或 ≥2 模块  → 3         ≥1 → 2         无 → 1
  ```
  跨模块边界、共享契约（`is_shared_contract`）会额外抬高 `change_scope` 和 `production_impact`。
- **模型语义**（`--intent-file`）——宿主模型读懂需求后，给出结构化 intent：相关文件、领域提示、变更性质。

**单调性约束（重要）**：模型只能**加**风险，**不能减**。硬性围栏一旦由强证据触发，模型说什么都降不下来。这条在代码里是不变量，不是约定。

### 2. 硬性围栏

某些领域一旦命中强证据，**直接锁死最低等级**，绕不过去：

```yaml
destructive-database-operation:  L4    # DELETE FROM / TRUNCATE / DROP TABLE
governance-bypass:               L4    # 改治理机制本身
financial-calculation-change:    L3
auth-security:                   L3
physical-device-control:         L3
public-interface-change:         L3
```

弱信号只作为「候选项」提示人确认，**不锁等级**——这是为了不让泛化的关键词命中变成硬事实。

### 3. 工作流路由

不同等级要求的模块不同（`workflow_composer.py`）：

| 等级 | 必需模块 |
|---|---|
| **L1** | `code_fact_scan`, `regression_test` |
| **L2** | + `requirement_confirmation`, `technical_design`, `test_design` |
| **L3** | + `dependency_analysis`, `independent_review`, `rollback_plan` |
| **L4** | + `data_impact_analysis`, `adversarial_review`, `staged_release`, `post_release_monitoring` |

模块覆盖是**诚实的，不自动**：只有通过 `--complete-step` 真正完成并交付产物的模块才标 `covered`，其余一律 `planned`。硬性围栏要求的分析模块没做完，技术方案就批不了。

### 4. 人工闸门 + 审计

流程会**主动停下来**等人：`workflow_plan_approval` → `technical_plan_approval` → `implementation` 闸门。每个 run 在 `.ai-governance/runs/<run_id>/` 下留完整痕迹：风险评估、证据包、工作流方案、审批记录、diff 校验、最终报告。

产物用 `FACT` / `INFERENCE` / `UNKNOWN` / `DECISION` 标注，**区分「查到的」和「猜的」**。

---

---

## 三、原理：一次评估内部发生了什么

`change-assess "<需求>"` 的完整链路（`cli.py:222-238`）：

```
需求文本 + (可选) intent 文件
        │
        ▼
┌───────────────────────────────────────────────────┐
│ 1. RepositoryAnalyzer.analyze()   —— 取证，不判分  │
├───────────────────────────────────────────────────┤
│  ① 定位  关键词匹配 ∪ 模型 relevant_files          │
│         → direct_files                            │
│  ② 取证  code_signals   扫 direct_files 的代码事实 │
│         reference_scanner  扫谁在引用被改的符号     │
│         file_risk       套 file_risk 规则          │
│         feature_boundary 分级证据强度 + UNKNOWN     │
│  ③ 合成  affected_domains =                       │
│           request_domains ∪ file_domains          │
│         ∪ code_signals.domains ∪ hint_domains     │
└───────────────────────────────────────────────────┘
        │  evidence-pack.yaml
        ▼
┌───────────────────────────────────────────────────┐
│ 2. RiskEvaluator.evaluate()       —— 判分          │
├───────────────────────────────────────────────────┤
│  ① 围栏  triggered_guardrails（区分强/弱证据）      │
│  ② 打分  10 维 × 权重 → weighted_score            │
│         影响面在这里改写 dependency_coupling       │
│  ③ 定级  calculated_level ← 阈值表                 │
│         final_level = max(calculated, 围栏最低级)  │◄── 单调性在这一行
└───────────────────────────────────────────────────┘
        │  risk-assessment.yaml
        ▼
┌───────────────────────────────────────────────────┐
│ 3. WorkflowComposer.compose()     —— 路由          │
│    required = LEVEL_MODULES[final_level]          │
│             ∪ 围栏 required_by_guardrails         │
│             → 按 request_goal 裁剪                 │
│    prohibited 作为禁止项一并输出（不从 required 扣）│
└───────────────────────────────────────────────────┘
        │  workflow-recommendation.yaml / workflow-plan.md
        ▼
    停在 workflow_plan_approval，等人
```

### 三个设计要点

**① 取证和判分是分开的。** `RepositoryAnalyzer` 只回答「代码里有什么」，不碰风险等级；`RiskEvaluator` 只读证据包，不碰仓库。所以证据包可以单独审、单独存，风险规则可以单独改而不动扫描逻辑。

**② 定位是「关键词 ∪ 模型」的并集，不是二选一。**

```python
direct_files = self._merge_model_files(self._find_relevant_files(request, files), intent, files)
```

关键词搜索管不了「中文需求 vs 英文代码」这道坎——「退款」搜不到 `refund_amount`。模型的 `relevant_files` 正是来补这个。两者取并集，**任何一边多找到的文件都会进入取证范围**。这也是为什么定位质量直接决定风险质量：定位漏了文件，`code_signals` 就扫不到那段代码。

**③ 单调性落在 `final_level = max(calculated_level, guardrail_minimum)` 这一行。**

围栏最低级只能**抬高**结果，永远不会拉低。模型 intent 唯一能降风险的入口是 `text_only_change`（`repository_analyzer.py:107`），而它**只认模型 intent、不认关键词**——代码注释把原因写得很明白：

> Display-text-only is a judgment about the intended change, which cannot be read from current code and must not be guessed from request wording: a keyword rule here is the one place where a literal match could *suppress* risk.

换句话说：**能让风险变低的地方，全项目只有一处，而且它要求模型显式表态、留下 `change-intent.yaml` 存档。** 其余所有信号都只能加风险。

### 影响面怎么进入分数

`reference_scanner` 数出引用后，`risk_evaluator.py:421-427` 做三件事：

```python
dimensions["dependency_coupling"] = fan_out          # 直接改写，不再是项目常量
if not display_only:
    dimensions["change_scope"] = max(dimensions["change_scope"], fan_out)
if reference.get("is_shared_contract") and not display_only:
    dimensions["change_scope"] = max(..., 4)
    dimensions["production_impact"] = max(..., 4)
```

注意 `dependency_coupling` 是**被赋值**而不是取 max——它从「项目级的一个拍脑袋常量」变成了「这次改动的实测扇出」。而 `change_scope` 和 `production_impact` 取 max，只升不降。

---

## 四、流程：从需求到合并

### 状态机

闸门状态由 run 目录下的**标记文件**表示，`TechnicalPlanGate.check_gate()` 检查它们（`technical_plan.py:161-180`）：

```
   change-assess "<需求>"
        │
        ▼
   ┌─────────────┐
   │  已评估      │  产出 risk-assessment / workflow-plan / review.md
   └─────────────┘
        │  ← 人工闸门 1
        │  --approve-workflow      写 .workflow-approved
        ▼
   ┌─────────────┐
   │ 工作流已批准  │  --generate-agent-tasks / --complete-step ...
   └─────────────┘
        │  --propose-technical-plan → --review-technical-plan
        │  ← 人工闸门 2
        │  --approve-technical-plan   写 .technical-plan-approved
        ▼                             （硬性围栏模块没做完则拒绝）
   ┌─────────────┐
   │ 技术方案已批准│
   └─────────────┘
        │  --check-gate --stage implementation
        │  ← 人工闸门 3（机器校验）
        ▼   要求：.workflow-approved
   ┌─────────────┐  ∧ .technical-plan-approved
   │  GATE OK    │  ∧ approved-technical-plan.yaml 存在
   └─────────────┘  ∧ diff-verification 不是 blocked
        │
        │  ← 只有到这里才允许改业务代码
        ▼
   ┌─────────────┐
   │  实施完成    │  --verify-diff → --reassess
   └─────────────┘  → --generate-verification-report
        │
        ▼
      PR → CI 门禁重新独立打分 → required check → 合并
```

**三道闸门的分工**：

| 闸门 | 谁判 | 判什么 |
|---|---|---|
| 1. `workflow_plan_approval` | 人 | 风险定级对不对、要走的流程合不合理 |
| 2. `technical_plan_approval` | 人 + 机器 | 方案本身；机器**强制**硬性围栏要求的分析模块已完成 |
| 3. `implementation` | 机器 | 前两道的状态文件齐全、diff 校验未阻塞 |

### 闭环：说过的话要兑现

流程不是单向的。批准时的承诺，实施后要拿 diff 验：

- `--verify-diff` — 把实际改动**逐文件**对比已批准的 `files_to_modify`。**范围外的文件改动 = 失败**（fail-closed：批准清单为空也算失败，不是「无限制」）。如果当初是靠 `text_only` intent 降的风险，这里会检查有没有出现像可执行代码的改动——**你说只改注释，那就得只改注释**。
- `--reassess` — 按改动后的仓库重新跑一遍评估，和初始评估对比。风险涨了会暴露出来。
- CI 门禁 — 在 PR 上**完全不看**需求文本和 intent，只看 diff 的代码事实重新打一次分。本地流程说过的话，在这里全部作废重来。

这就是整套设计的骨架：**声明 → 批准 → 实施 → 用实际 diff 验证声明 → 服务端独立复核。** 每一层都不信任上一层的自述。

---

## 五、怎么用

### 安装

```bash
python3 -m pip install PyYAML
```

**Claude Code 插件**（推荐路径）：

```text
/plugin marketplace add <repo-root>
/plugin install adaptive-change-governance@adaptive-governance
/reload-plugins
```

然后直接说需求：

```text
/adaptive-change-governance:change-assess 修改后台订单页面上的提示文案。
```

**Codex 插件**：

```bash
codex plugin marketplace add IrvingSun/adaptive-change-governance --ref main
codex plugin add adaptive-change-governance@adaptive-governance
```

装完后新开会话，让 Codex 在技术方案和实施前使用 Adaptive Change Governance。两个插件共用同一套 `change-assess` CLI 运行时。

### 主流程

在目标仓库根目录：

```bash
change-assess "把后台「群配置」相关的菜单修改为「业务群配置」"
```

（本仓库开发时用 `bin/change-assess`。）

命令输出 run id，先看给人读的两个文件：

```bash
cat .ai-governance/runs/<run_id>/review.md
cat .ai-governance/runs/<run_id>/workflow-plan.md
```

**不用背命令**，让工具告诉你下一步：

```bash
change-assess --status <run_id>      # 全景仪表盘
change-assess --next <run_id>        # 单条下一步建议
change-assess --continue <run_id>    # 自动推进所有安全步骤，停在下一个人工闸门
```

完整的实施路径：

```bash
change-assess --approve-workflow <run_id>
change-assess --propose-technical-plan <run_id>
change-assess --review-technical-plan <run_id>
change-assess --approve-technical-plan <run_id>
change-assess --check-gate <run_id> --stage implementation   # 必须返回 GATE OK
```

**只有拿到 `GATE OK` 才能动业务代码。** 实施完成后校验实际 diff：

```bash
change-assess --verify-diff <run_id>                    # 实际 diff 对比已批准方案
change-assess --reassess <run_id>                       # 按改动后的仓库重新评估
change-assess --generate-verification-report <run_id>
```

只是要分析、不改代码：

```bash
change-assess --generate-analysis-report <run_id>
```

### 需求类型（`request_goal.type`）

把「用户想要什么」和「实施风险」分开：

- `implementation` — 要改代码，走完整流程。
- `analysis_only` — 只要事实/风险分析，停在 `analysis_complete`。
- `decision_support` — 给建议供用户决策，停在 `decision_ready`。
- `planning_only` — 出技术方案但不改代码，停在 `technical_plan_approval`。

### 模型 intent 契约

宿主模型先推断结构化 intent 再传给 CLI：

```yaml
version: 1
change_kind: menu_label_change
change_nature: display_text_only
summary: rename one menu display label
confidence: high
request_goal:
  type: implementation
  requires_code_change: true
  default_stop_gate: workflow_plan_approval
  rationale: "INFERENCE: user asks to modify repository behavior or files"
scope:
  included: [menu display text]
  excluded: [database changes, public API changes]
  unknowns: []
risk_hints:
  data_operation: false
  financial_change: false
  public_interface_change: false
  permission_change: false
  security_change: false
  database_schema_change: false
```

```bash
change-assess "修改需求描述" --intent-file change-intent.yaml
```

> **注意一个真实代价**：`text_only` 判定**只认模型 intent**，没有关键词兜底（这是刻意去掉的）。所以**不带 intent 跑琐碎改动会过度升级**。实测（同一个仓库、同一句需求，只差 intent 文件）：
>
> | 需求 | intent | 结果 |
> |---|---|---|
> | `修改 app/database.py 中的一行注释` | 无 | **L3（35.7 分）** |
> | `修改 app/database.py 中的一行注释` | `comment_only` | **L1（11.3 分）** |
>
> 这是「拒绝用关键词猜」的直接代价：**插件的判断力依赖宿主模型配合**，纯 CLI 裸跑会显著偏保守。另外注意升级**取决于定位是否命中代码文件**——同样一句话，如果定位只匹配到 `README.md` 这类非代码文件，`code_signals` 不出结果，反而会是 L1。**风险结论对目标仓库的实际内容敏感，不要拿别的仓库的分数外推。**

### 配置

| 文件 | 管什么 |
|---|---|
| `.ai-governance/project-risk.yaml` | 基线风险、`file_risk` 文件重要性规则、审计保留策略 |
| `.ai-governance/guardrails.yaml` | 硬性围栏：资金、删数据、权限、公共接口 |
| `.ai-governance/workflow-modules.yaml` | 各等级要求哪些模块 |
| `.ai-governance/risk-calibration.yaml` | 阈值和权重覆盖 |
| `.ai-governance/profiles/<profile>/` | 领域特化覆盖 |

`file_risk` 让「同样大小的改动」路由不同：

```yaml
file_risk:
  - pattern: "app/database*.py"
    level: high
    reason: database connection or persistence infrastructure
  - pattern: "frontend/src/layouts/**"
    level: low
    reason: navigation and display shell
```

文件风险有两层：`highest_level`（文件固有重要性）和 `effective_level`（结合变更 intent 后的实际风险）。`app/database.py` 里的纯注释改动保持 `highest_level: high` 但可以按 `effective_level: low` 路由——同时会加一条 UNKNOWN，**要求实施 diff 自证确实只改了注释**。

给新仓库自举 `file_risk` 规则：

```bash
change-assess --suggest-risk-config                        # 只写建议，不改配置
change-assess --suggest-risk-config --apply-risk-config    # 需显式确认
```

其他：

```bash
change-assess --validate-risk-scenarios                    # 校验打分校准
change-assess "..." --profile charging-platform            # 用充电平台 profile
change-assess --cleanup-runs --cleanup-dry-run             # 清理旧 run（先预览）
```

### 多 agent 协作

L3/L4 的 `agent-tasks.yaml` 会把 subagent 标为必需，并把只读事实收集、依赖/数据影响审查、对抗审查、技术方案审查、闸门后实施**分开**。subagent 必须引用证据，且**除非在 `GATE OK` 后被指派实施模式，否则不得改业务代码**。

每个任务自带 `completion_command`：

```bash
change-assess --complete-step <run_id> --module dependency_analysis \
  --artifact dependency-analysis.yaml --agent dependency-analyzer
```

如果 `.ai-governance/artifact-schemas.yaml` 为该模块定义了 schema，`--complete-step` 会先校验产物才标记完成。严格模块要求结构化证据：

```yaml
evidence:
  - path: app/api/example.py
    line: 12
    fact: "FACT: caller references the changed endpoint"
    confidence: high
```

---

## 六、它到底能守住多少

**四层阶梯，按需采纳，每层独立有用。别把后面的当前提。**

### 第 1 层：本地 hook —— 流程纪律，不是边界

Claude Code 插件注册 `PreToolUse` hook（`hooks/implementation_gate.py`）：实施目标的 run 没过 `--check-gate --stage implementation` 之前，对项目文件的 `Edit`/`Write` 会被拒绝；`.ai-governance/runs/` 下的闸门状态文件只能经 CLI 写入。`ACG_HOOK_MODE=warn` 改为只记录，`=off` 关闭。

它防的是「agent 图省事跳过流程」和「手滑改错文件」。**它不是墙**：

- **只拦编辑类工具**（`Edit|Write|MultiEdit|NotebookEdit`）。shell 写入拦不到——包括写闸门状态文件，`echo "" > .ai-governance/runs/<run>/.workflow-approved` 就能伪造审批。
- **它能关掉自己**：`ACG_HOOK_MODE=off` 是个环境变量，agent 自己就能 export。
- **Codex 侧根本没有 hook**：`.codex-plugin/plugin.json` 只声明 `skills`，那边 SKILL 就是全部的闸门。

扩到 `Bash` 也补不上：拦任意 shell（`>`、`tee`、`sed -i`、`python -c`、heredoc、生成的脚本）是打地鼠，而且**在同一台机器上有 shell 权限的 agent，本来就不可能被本地文件闸门约束住**。

### 第 2 层：CI 门禁 —— PR 风险复核

```bash
change-assess --ci-gate origin/main --ci-fail-level L3 --ci-output gate-summary.md
```

`--ci-gate` 对着 base ref 重新给 diff 打分，**只看代码事实**——不读需求文本、不读 intent 文件，所以工作区里没有任何东西能把结论说软。领域来自改动代码的 `code_signals`，破坏性操作来自 diff **新增**的语句，影响面来自 `reference_scanner`，加上配置的 `file_risk`。

达到 `--ci-fail-level`（默认 `L3`）时退出码 `3`，含义是**「这个该有人看看」，不是「这个改错了」**。

它的价值：**哪怕 agent 把本地流程全跳了，PR 在合并前仍然拿到一份统一、可见、可审计的结论。** `.github/workflows/change-governance.yml` 在每个 PR 上跑。

> **已知自指现象**：对**本仓库自己**跑，门禁会报 `financial-calculation` 和 `physical-device-control`——因为它自己的测试夹具和模式定义里字面写着 `power_off(...)` 和 `round(price * qty, 2)`。这是自指，不是对正常目标仓库的发现。

### 第 3 层：required status check —— 让结论真的挡住合并

在分支保护里把 `governance-gate` 设为必需检查。**没做这步，第 2 层只是条评论，不是门禁。**

这个检查被设计成**可清除的**，这正是第 3 层可用的原因：GitHub **不允许 review 批准去覆盖失败的必需检查**，所以一个「高风险就失败」的门禁会让每个 L3/L4 的 PR **永远无法合并**。实际做法是：门禁报 `review_required` 时，workflow 去查该 PR 的 reviews，**有作者以外的人批准过就放行**。同时监听 `pull_request_review` 事件——因为 GitHub 在 review 落地时**不会**重跑 `pull_request` workflow，没有这个触发器检查永远清不掉。

净效果：**低风险 PR 无感合并，高风险 PR 等人。**

### 第 4 层：CODEOWNERS —— 只在你需要更强威胁模型时

对付的是：**一个 PR 把门禁本身、规则、或 workflow 改掉，让门禁给自己放行。首次落地不需要这步。**

已经免费带上、零配置的两层：

- workflow 在 `github.event.pull_request.base.sha` 检出工具，**跑那个版本的代码**，用 base 版本的规则（**绝对路径**传入——用相对路径时 `change-assess` 会优先读项目自己的 `.ai-governance/`，那就把规则又交回给 PR 了），去评 PR 的代码树。**在 PR 里改 `ci_gate.py` 或 `guardrails.yaml` 不会改变这个 PR 被怎么评。**
- 那个 base 版本的门禁，对任何碰到 `GOVERNANCE_PATHS` 的 diff（workflows、`bin/change-assess`、`lib/adaptive_change_governance/**`、`plugins/**`、`.ai-governance/**`、CODEOWNERS）**无条件强制 `review_required`**，不管算出来几级。

**剩下的洞是 workflow 文件本身**——而上面那两层就住在这个文件里。对 `pull_request`，GitHub 跑的是**来自 PR 的** workflow 定义，所以**它不能做自己的信任锚**。只有一个仓库设置能补上：启用 **Require review from Code Owners**，配合 `.github/CODEOWNERS`（已附带，替换 `@OWNER`）覆盖治理路径。

> **澄清一个常见误解**：`.github/CODEOWNERS` 只圈了**治理工具自己的文件**，一行业务代码都没有。它管的不是「谁写的代码谁批」，而是「**谁看守闸门**」。改同事写的业务代码不会触发它。

### 关于 GitLab

`change-assess --ci-gate` 本身**平台无关**，GitLab 能用。但 `.github/workflows/`、`pull_request_review` 触发器、`gh api` 查 review **全是 GitHub 特有的**，需要重写成 `.gitlab-ci.yml` + GitLab API。且「按风险等级动态决定要不要审批」在 GitLab 上更难——原生审批规则不能由 CI 算出的等级动态开关。**这块尚未实现，也未验证。**

---

## 七、优点与局限

### 优点

- **风险判断基于代码事实和引用扇出，不是关键词匹配。** 这是与同类工具最本质的区别。
- **确定性有下限。** 不传 intent 时走纯确定性打分，可复现、可测试（`--validate-risk-scenarios`，当前 5/5 通过）。
- **模型只能加风险不能减。** 单调性是代码不变量。
- **诚实的覆盖度。** 没做的模块就是 `planned`，不会自动标绿。
- **CI 门禁由 base 版本执行**，PR 改不动评判自己的裁判。
- **完整审计链**，且区分 FACT / INFERENCE / UNKNOWN。
- **高度可配置**，围栏、阈值、文件风险、profile 都能按项目调。

### 局限（如实说）

- **阈值和权重是 MVP 拍脑袋定的。** `risk-calibration.yaml` 里 `source: default-mvp-calibration` 就是字面意思。「比纯人工判断更稳定」目前**没有证据支撑**，需要用你们自己的历史事故去校准。
- **不带模型 intent 会过度升级琐碎改动**（见上文 L3 35.7 vs L1 11.3 实测）。反过来，**定位没命中代码文件时又会漏判成 L1**——两个方向的偏差都存在，且都取决于目标仓库的实际内容。
- **本地 hook 不是边界**，shell 能绕过，agent 能关掉，Codex 侧根本没有。
- **模型自报的 confidence 无法验证**，只能当提示。
- **自指误报**：对治理工具类仓库自己跑会误报领域。
- **真实 GitHub PR 端到端演练尚未做过。** 第 2–4 层的逻辑经过本地测试和代码审查，但**没有在真实 PR 上跑通过**。这是当前最大的未验证项。
- **GitLab 侧的 CI 接线不存在。**

### 适合 / 不适合

**适合**：有明确高风险域（资金、设备控制、数据删除、公共接口）的团队；已经在用 AI agent 改生产代码；需要留审计痕迹；PR 流程规范。

**不适合**：想要安全沙箱的（这不是）；纯个人小项目（流程开销不划算）；不用 GitHub PR 且不打算自己接 CI 的；期望开箱即用零校准的。

---

## 八、开发

根目录的 `lib/`、`bin/`、`.ai-governance/*.yaml` 是**唯一事实源**；`plugins/adaptive-change-governance/` 是副本。改完后必须同步：

```bash
scripts/sync-plugin.sh
```

`test/test_phase1.py` 会在副本漂移时失败。CI（`.github/workflows/ci.yml`）跑 `mypy`（见 `mypy.ini`）和测试套件。本地跑同样的门禁：

```bash
python3 -m pip install PyYAML mypy types-PyYAML
scripts/check.sh   # mypy + tests，与 CI 一致
```

单独跑：

```bash
mypy lib/adaptive_change_governance plugins/adaptive-change-governance/hooks/implementation_gate.py
python3 test/test_phase1.py
```

### 打包

**从 Git 打包，不要从工作区打包**：

```bash
git archive HEAD plugins/adaptive-change-governance | tar -x -C <dest>
```

`git archive` 只出跟踪的文件，`__pycache__` 之类的忽略产物混不进去。直接拷目录会带上——跑测试或 hook 随时会在 `plugins/` 里重新生成字节码。`scripts/sync-plugin.sh` 会清，但那只保证到下次跑测试为止，**不要指望打包那一刻工作区是干净的**。

### 本地插件调试

```bash
claude --plugin-dir ./plugins/adaptive-change-governance
```

- Claude Code marketplace：`.claude-plugin/marketplace.json`
- Codex marketplace：`.agents/plugins/marketplace.json`

插件向 Claude Code 的 Bash PATH 注入 `change-assess` 可执行文件，要求 `python3` 环境里有 `PyYAML`。
