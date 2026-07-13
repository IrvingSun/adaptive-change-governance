# /change-assess

统一入口：

```bash
bin/change-assess "<需求描述>" --mode assess
```

可选 profile：

```bash
bin/change-assess "<需求描述>" --profile charging-platform
```

阶段一只实现 `assess`：

1. 读取 `.ai-governance/project-risk.yaml`。
2. 读取 `.ai-governance/guardrails.yaml`。
3. 读取 `.ai-governance/workflow-modules.yaml`。
4. 校验配置结构。
5. 检查 Git branch、commit、status、diff；不可用时写入 `UNKNOWN`。
6. 分析用户诉求并扫描当前版本代码。
7. 生成 `evidence-pack.yaml`。
8. 应用硬风险围栏。
9. 生成 `risk-assessment.yaml`。
10. 生成 `workflow-plan.md`。
11. 生成 `review.md` 和 `human-review.yaml`。
12. 停止，等待 `workflow_plan_approval`。

人工审阅：

```bash
bin/change-assess "<需求描述>"
# 编辑 run 目录中的 human-review.yaml
bin/change-assess --approve-workflow <run_id>
```

`human-review.yaml` 支持：

- `decision: approve | reject | request_changes | reassess`
- 提高 `risk_override.final_level`
- 追加 required / optional 流程模块
- 补充用户事实和修正 AI 推断

禁止：

- 降低 AI 或硬围栏确定的最终风险等级
- 删除硬围栏或最终等级要求的必选模块
- 跳过 `workflow_plan_approval`

边界：

- 未确认 `workflow-plan.md` 前，不得生成 `technical-plan.md`。
- 未确认 `technical-plan.md` 前，不得执行实现。
- 硬围栏命中后只能提升等级或追加模块，不能降级或删除必需门禁。
