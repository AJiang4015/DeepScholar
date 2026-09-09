# F9-P0 Batch 8 — Stopping / Threshold Calibration Implementation Report

> 状态：**BATCH 8 PASS / FROZEN（用户 2026-10-01 正式 Freeze；branch
> `feature/f9-batch8-calibration`；Git Commit / Push / Merge 待用户另行批准）**。
> 依据：用户 2026-10-01 批准（D1–D4 Decision Closure CLOSED；BUDGET_NEAR_RATIO 裁决 =
> 非 frozen protocol，改值允许同步行为边界测试）；Batch8 Readiness/Decision Closure
> `docs/plan/2026-10-01-f9-p0-batch8-readiness-review.md`。Git Workflow：全程在
> **`feature/f9-batch8-calibration`** branch。
> 本批不 Freeze / Commit / Push / Merge（等待 User Review）。

## 1. Deliverables

- 新增 `app/f9/eval/calibration.py` — Stopping/Threshold Calibration runner（确定性参数
  敏感性扫描；不改生产默认值文件；运行期覆盖参数）；
- 新增 `tests/test_f9_calibration.py`（4 tests）— runner 恢复机制 / summary·sensitivity
  结构 / MIN_EVIDENCE 轮次单调性 / D3 默认值 Pareto 判定；
- 证据：`_testtmp/b8_evidence.json`（gitignored，61 点扫描原始汇总）；
- 本报告。
- 零修改：F8/F1–F7/Batch1–7 / 生产默认值文件 / Batch7 eval 契约 / schema·migration /
  UI / infra / Agent topology。

## 2. Calibration Experiment Design（执行事实）

- 复用 Batch7 eval harness（world/agents/harness/rubric/scenarios），不改其契约；
- 参数扫描：5 参数 × 2–3 档 × 5 代表场景（s1/s2/s6/s7/s8）= 61 观测点；每点独立
  research db + governance（无跨点污染）；
- 参数覆盖方式：MIN_EVIDENCE / BUDGET_NEAR_RATIO 经 gaps 模块属性 monkeypatch（运行期），
  3 个 policy 值经 `research_policy` 注入 —— **均不改生产默认值文件**；
- 每档结束恢复默认值（测试断言恢复）；
- baseline 对照语义由场景自带（s1/s2 双跑语义沿用 Batch7）；观测 rubric total/
  required/citation + rounds/stopped/plans + cost compliance。

## 3. Parameter Search Space

| 参数 | 扫描档 | 默认 |
|---|---|---|
| min_evidence | 1 / 2 / 3 | 2 |
| budget_near_ratio | 0.1 / 0.2 / 0.3 | 0.2 |
| max_research_rounds | 2 / 3 / 4 | 3 |
| max_no_progress_rounds | 1 / 2 | 1 |
| repeated_gap_threshold | 2 / 3 | 2 |

## 4. World / Scenario Matrix

s1（sufficient/no-followup, control）、s2（missing required, positive）、s6
（diminishing/stop, control）、s7（near-budget, control）、s8（judge-failure fallback,
negative）。每场景每参数档独立跑 adaptive（真实 orchestrator + scripted world/judge/plan/
search）。多场景交叉 + 多 world（每场景自带 world seed）防单 world 过拟合。

## 5. Baseline vs Adaptive Results / Quality / Cost / Stopping Correctness

证据（61 点汇总，`_testtmp/b8_evidence.json`）：

| param | value | avg_rounds | avg_total | avg_required | avg_citation | cost_compliant |
|---|---|---|---|---|---|---|
| budget_near_ratio | 0.1/0.2/0.3 | 3.0 | 6.6 | 0.8 | 0.8 | True |
| max_no_progress_rounds | 1/2 | 3.25 | 6.5 | 0.75 | 0.75 | True |
| max_research_rounds | 2/3/4 | 3.0 | 6.6 | 0.8 | 0.8 | True |
| min_evidence | 1 | 2.6 | 6.6 | 0.8 | 0.8 | True |
| min_evidence | 2 | 3.0 | 6.6 | 0.8 | 0.8 | True |
| min_evidence | 3 | 3.4 | 6.6 | 0.8 | 0.8 | True |
| repeated_gap_threshold | 2/3 | 3.25 | 6.5 | 0.75 | 0.75 | True |

结论：
- **质量与成本对 5 参数不敏感**（avg_total 恒定 6.6/6.5、cost 全合规）；
- **唯一可测影响 = MIN_EVIDENCE 对 stopping 轮次**（1→2.6 / 2→3.0 / 3→3.4 且出现
  no_progress）；max_research_rounds≥3 未成瓶颈（实际 ≤3 轮收敛）；
- **stopping correctness**：全档 completed 收敛（no_gap / adaptive_fallback），无
  timeout/cancel；repeated/no-progress 仅 ME=3 触发 no_progress（僵局语义正确）。

## 6. Pareto Analysis / Recommended Parameters / Default Change Decision

- Pareto 判定：默认值（ME=2, ratio=0.2, rounds=3, no_progress=1, repeated=2）质量不劣于
  任何邻档（avg_total 相同）、成本合规、轮次适中。
- **推荐参数：维持现状默认值** —— 无证据表明其他档严格更优：降低 MIN_EVIDENCE 到 1
  仅省 0.4 轮且放宽 required 语义（rubric 未惩罚但语义更松）；提升无质量收益。
- **是否修改默认值：否**（符合 D3："不预设改码；实验不支持则保持"；禁止为 diff 调参）。

## 7. Before/After Evidence

- before（现状默认）：avg_rounds 3.0 / avg_total 6.6 / cost compliant（本批默认档实测）；
- after（若改 ME=1）：avg_rounds 2.6 / avg_total 6.6 —— 质量不变仅省轮，语义更松；
- after（若改 ME=3）：avg_rounds 3.4 / avg_total 6.6 —— 质量不变仅增轮 + no_progress；
- **结论：无质量/cost 收益的默认值改动 → 不改**（before/after 全在 `b8_evidence.json`）。

## 8. Verification

- sqlite 全量：**731 passed / 88 skipped / 0 failed**（= 727 既有 + Batch8 4；排除 mysql
  污染文件）。
- PG 全量 14 postgres 文件：**81 passed / 0 failed**（不受影响）。
- Batch7 deterministic Gate：**PASS**（8 scenarios 测试在全量回归中全绿，未改 eval 契约）。
- ruff / compileall（app/f9/eval + calibration）：全绿。

## 9. Batch7 Gate / Compatibility Gap

- Batch7 eval 契约未改；8 scenarios 测试原样通过 → Gate PASS。
- **COMPATIBILITY GAP：无**。未改生产默认值 → 未触发 BUDGET_NEAR_RATIO 裁决的改值分支；
  未触碰 Batch2/6/7 frozen semantics；无 schema/migration/UI/infra/Agent 改动。

## 10. Limitations

1. deterministic scripted 场景对多数参数不敏感（avg_total 恒定）→ 敏感性主要来自
   stopping 轮次；real-provider 上的参数影响未验证（无凭据，supplementary limitation）。
2. rubric 未对"required 语义过松"（ME=1）设惩罚 → 质量不敏感部分源于 rubric 粒度；
   校准报告如实记录，不据此改值。
3. 校准只覆盖 Batch7 8 场景子集（5 个代表性场景）；更多世界/语料需 future Eval 扩展。

## 11. Exact Changed Files

```
app/f9/eval/calibration.py          (新增，calibration runner)
tests/test_f9_calibration.py        (新增，4 tests)
docs/plan/2026-10-01-f9-p0-batch8-implementation-report.md  (本报告)
（PROJECT_CONTEXT.md 在本批为 Branch8 状态，随 Freeze 后同步——当前工作树含既有 readiness 变更）
```

## 12. Git Status

```text
branch: feature/f9-batch8-calibration
working tree: calibration.py + test + report + readiness（未 commit）
diff --check: clean（待最终复核）
```

## 13. Final Decision

```text
F9-P0 Batch 8 — Stopping / Threshold Calibration
STATUS: PASS / FROZEN（2026-10-01 用户正式 Freeze）
branch: feature/f9-batch8-calibration
```

- Freeze 内容确认：calibration runner 完成；5 参数实验完成；61 observation points 完成；
  推荐值 = 保持现状默认值；无 production default 修改；无 frozen contract 修改。
- 验收：sqlite 731 passed / 88 skipped / 0 failed；PG 81 passed；Batch7 Gate PASS；
  calibration tests 4/4 PASS；ruff / compileall PASS；Compatibility Gap = None。
- Git 状态：工作树保持 Batch8 scope（未 Commit / Push / Merge）；Git Commit 待用户明确
  批准后执行（AGENTS.md §10 / PROCESS.md §12 流程）。
