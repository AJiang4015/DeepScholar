# Harness 文档治理报告 — Context Governance / 文档职责优化

> 状态：**HARNESS DOCUMENT GOVERNANCE — PASS**（2026-09-22）。
> 目标：建立 规范层 → 架构层 → 当前状态索引层 → 任务层 → 实现/验证证据层 的职责模型，
> 让新 DSH 在只读 4 个核心文件 + 相关 Feature Spec/Batch Plan/按需 Architecture·Decision 的
> 情况下即可低上下文成本恢复项目状态；不破坏既有规范/架构/决策/验证体系。
> 改动授权范围：仅 `PROJECT_CONTEXT.md`（新增）+ `AGENTS.md` + `PROCESS.md`（最小修改）+
> 本报告。其余文档发现的问题一律只记录、不擅改。

---

## 1. Current Documentation Architecture

```text
规范层（Normative Contracts）       AGENTS.md（行为）/ PROCESS.md（状态机+Gate）/
                                    TESTING.md（验证契约）/ ARCHITECTURE.md（架构红线）/
                                    PROBLEM.md（问题注册索引规则）/
                                    docs/spec/（Feature 语义契约，FROZEN 者为冻结契约）/
                                    DECISION.md 的 Constraints Created
架构层（Architecture Facts）         ARCHITECTURE.md（止于 F7）
                                    + F8/F9 架构事实暂居 docs/plan freeze review 与 F9 Spec
当前状态索引层（Current State）      PROJECT_CONTEXT.md（新增；非规范）
任务层（Active Task/Feature/Batch） 当前 Feature 的 docs/spec/ + 当前 Batch 的 docs/plan/
实现/验证证据层                      tests/ + docs/plan/ 下的 gate/implementation/final reports
                                    （仓库无 reports/ 目录；历史证据由 docs/plan 兼任）
问题层                              PROBLEM.md（索引）→ docs/problem/（Detail）
历史决策层                          DECISION.md D001–D017；F8+ 决策暂在 docs/plan freeze review
```

## 2. Responsibility Audit（逐文档主职责 + 归类）

| 文档 | 唯一主职责 | 归类 |
|---|---|---|
| AGENTS.md | Agent 行为规则（含 Authority 顺序、Document Loading Policy） | 规范·行为 |
| PROCESS.md | 强制执行状态机 + 两个 Gate | 规范·流程 |
| PROBLEM.md | 问题注册表**索引**（P001–P006 状态 + 登记/更新规则） | 索引 + 登记规范 |
| TESTING.md | 验证契约（手段事实/类型矩阵/安全矩阵/完成与 Harness 审查） | 规范·验证 |
| ARCHITECTURE.md | 系统结构约束与红线（现覆盖至 F7） | 架构 |
| DECISION.md | D001–D017 长期决策与 Reasoning（止于 F7） | 历史决策 |
| docs/problem/ | 单问题 Detail | 历史问题细节 |
| docs/spec/ | 每 Feature 的"必须实现什么"（多带 FROZEN/PASS 状态） | 规范·Feature 契约 |
| docs/plan/ | 每 Feature/Batch"怎么实现 + 实现/验证证据" | 计划 + 证据 |
| PROJECT_CONTEXT.md（新） | **当前状态索引 + Navigation**（非规范） | 状态索引 |

10 问结论（详细版见会话审计）：职责、规范/架构/决策/状态/任务/证据归属如上；**每次任务必读** =
AGENTS + PROJECT_CONTEXT + PROCESS + PROBLEM（+命中 Problem Detail / 相关 Architecture·Decision /
当前 Spec·Batch Plan / Testing 章节）；**默认不读** = 历史 spec/plan/problem、历史 report、
历史 Decision、ROADMAP.md、根目录历史评审/测试报告。

## 3. Problems Found（只记录，未擅改；除授权三文件外零改动）

1. **TESTING.md 头部过期**（引言仍写"无自动化测试框架（P006）— 不存在 tests/、无 pytest"，
   与同文件 §1 后续行及实际 592 项测试矛盾）。
2. **PROBLEM.md P006 行过期**（Summary 仍称"无 tests/、无 pytest"；实际残余仅为 pytest 未声明
   进 pyproject/uv.lock）。
3. **DECISION.md 止于 D017**：F8/F9 决策未入 DECISION.md（无 D018/D019），实际落点在
   docs/plan/2026-09-18-f8-runtime-final-architecture-freeze-review.md 与 F9 Spec/Plan Rev。
4. **ARCHITECTURE.md 落后于真实仓库**：缺 app/runtime/governance（F8）与 app/f9（Batch 1）；
   F8 架构事实未回填（HARNESS REVIEW 项"ARCHITECTURE.md 反映真实仓库"当前不满足）。
5. **ROADMAP.md 停滞过期**（头部状态与其自身追踪行矛盾；已被 F 系列阶段取代；无人引用）。
6. **docs/plan 混载"计划"与"证据报告"两类职责**（无 reports/ 目录）。
7. **AGENTS/PROCESS 旧阅读条款未含 PROJECT_CONTEXT**（本次已同步修复，不属遗留）。

处置：以上 1–6 需后续单独治理（建议优先级：2→1→4→3→5→6）；本次不搬迁、不重写
（用户指令：不为"文档整洁"大规模重构 DECISION.md；发现先行记录）。

## 4. PROJECT_CONTEXT Design

- **定位**：Current State Index + Navigation。只做"索引和摘要"，指向权威文档；不定义规则。
- **非规范条款（写入 §0）**：`PROJECT_CONTEXT.md` is a current-state index, not a normative
  authority. It MUST NOT override AGENTS.md / PROCESS.md / ARCHITECTURE.md / TESTING.md /
  DECISION.md / active Spec contracts.（不进入 AGENTS.md §1 Authority 顺序——顺序未改。）
- **结构**（§1–§12）：Current Status / System in One Page / Identity Model / Frozen
  Architecture（索引+指针）/ Current F9 Contract / Current Batch / F9 Roadmap /
  Important Known Limitations / Verification Baseline / Important Files /
  Current Next Action / Context Maintenance Rules。
- **当前状态准确度**：Batch 1（Projection）已实现并 BATCH 1 PASS（`docs/plan/2026-09-21-f9-p0-batch1-implementation-report.md`），
  等待用户 Review/冻结；F9-P0 未 COMPLETE；Batch 2–8 未开始；禁止自动进入下一批。
- **无过期状态**：不保留 ROADMAP R 阶段、"无自动化测试"等历史断言；未复制任何 Spec/Architecture/
  Decision/测试日志大段文本（全为指针）。

## 5. AGENTS Changes（最小增强，2 处）

1. §3 起始 bullet："每次任务开始：读 `PROJECT_CONTEXT.md`（如存在；当前状态索引，非规范来源）、
   `PROCESS.md` 与 `PROBLEM.md`…"（其余语义不变）。
2. §3 新增 `### Document Loading Policy`：First read 集合 → selective read → MUST NOT 默认遍历
   全部历史 → 冲突处理（按 §1 Authority、报告冲突、不得覆盖权威）＋英文规范句。
   **未改动**：§1 Authority 顺序、§4 MUST NOT、§5 最小修改、§6 安全、§7 完成条件、§9 Harness 演进。

## 6. PROCESS Changes（最小修改，2 处；状态机未动）

1. §1 INTAKE Required Actions 增加：读 `PROJECT_CONTEXT.md`（如存在）作为当前状态索引
   （非规范来源，冲突按 AGENTS.md §1 处理）。
2. §2 INSPECTION Required Actions 首条：依据 PROJECT_CONTEXT 判定 Feature/Batch/状态，只加载
   相关文档（从"每次遍历仓库全部历史"改为"先读索引，再按需加载权威文档"）。
   状态机 `INTAKE→INSPECTION→PLAN→IMPLEMENTATION→VERIFICATION→REVIEW→COMPLETE` 与返回边、
   PREFLIGHT/COMPLETION Gate 文本均未改动。

## 7. Authority / Conflict Model

- 顺序保持 AGENTS.md §1（User Request > AGENTS > PROCESS > PROBLEM/Active Problem >
  ARCHITECTURE > TESTING > DECISION > Existing Implementation > Agent Preference）。
- PROJECT_CONTEXT **不进入**该顺序：索引层低于任何权威文档；冲突必报告，不静默选择。
- 新增冲突条款同时落于 AGENTS.md §3 Document Loading Policy 与 PROCESS.md §1，指向 §1 处理。

## 8. Context Loading Strategy

- First read（4 个核心 + 命中项）：AGENTS.md → PROJECT_CONTEXT.md → PROCESS.md → PROBLEM.md。
- Selective：命中 Problem Detail / 相关 ARCHITECTURE·DECISION 章节 / 当前 Feature Spec /
  当前 Batch Plan / TESTING 相关章节。
- Forbidden（无任务关联时）：历史 docs/spec、docs/plan、docs/problem、历史 report、
  历史 Decision、ROADMAP.md、根目录历史评审/测试报告。
- 冲突路径：PROJECT_CONTEXT vs 权威 → 报告并按 AGENTS.md §1 处理。

## 9. Token Efficiency Rationale

- 旧行为：每次任务 INSPECTION 遍历并可能通读大目录（docs/spec ~14 份、docs/plan ~12 份、
  docs/problem 6 份、DECISION.md ~477 行、ARCHITECTURE ~150 行、TESTING ~216 行…），
  且"当前状态"散落在多份 dated 文档头部，恢复成本高、易把历史当现状。
- 新行为：固定先读 4 个核心文件（~200 行总量级，其中 PROJECT_CONTEXT 为约 200 行的低密度索引），
  由 PROJECT_CONTEXT 给出 Feature/Batch/状态/Next Action/权威指针/必读与禁读清单；
  之后只加载当前 Feature Spec + 当前 Batch Plan + 按需章节。
- 收益可度量项：默认扫描目录数从"全部 docs/ 子树"降为"1 个索引文件 + 2–4 个被指文件"；
  "当前状态"从分散多文档收敛为单点（PROJECT_CONTEXT §1/§6/§11）；失效历史（ROADMAP 等）明确禁读。
- 代价与护栏：索引会过时 → §12 维护规则 + 冲突上报条款 + 定期（每 Feature/Batch 结束时）更新。

## 10. Verification Evidence

### V1 — 文档职责（每核心文档唯一主职责）
PASS。见 §2 职责表；PROJECT_CONTEXT 主职责单一（当前状态索引），未承载其它职责。

### V2 — Authority（PROJECT_CONTEXT 不覆盖权威）
PASS。AGENTS.md §1 顺序 diff 未动；PROJECT_CONTEXT §0 自述非规范 + 冲突条款；
AGENTS §3 与 PROCESS §1 均加"索引非规范/冲突按 §1 处理"。

### V3 — Loading Policy
PASS。AGENTS §3 Document Loading Policy + PROCESS §1/§2 落地；新 Agent 恢复路径走查：
PROJECT_CONTEXT §1(状态)→§10(文件地图)→§11(Next Action)→按 §6 打开 Batch1 报告/测试指针，
全程无需扫描历史 docs。

### V4 — Current State 准确
PASS。与仓库实态一致（核读）：F8 Step1–4 + Final Freeze Review 文档存在且 FROZEN；
F9 Spec Rev2/Plan Rev2/Readiness PASS；Batch1 报告存在且 BATCH 1 PASS（sqlite 36 + PG 8 +
回归 592/0）；Batch 2–8 未开始；§1/§6/§9/§11 相互一致。

### V5 — No stale state
PASS。PROJECT_CONTEXT 不含 ROADMAP R 阶段、P006"无测试"类历史断言或已完成阶段作为当前状态；
仅有的限制条目为仍真实存在者（multi-instance/sweeper/polling/凭据受限/live vs durable/mysql
污染文件），并经核读确认未过期。

### V6 — No duplication
PASS。PROJECT_CONTEXT 未大段复制 ARCHITECTURE/DECISION/TESTING/任何 Spec；全文为索引行+指针
（逐节人工比对）。

### V7 — Harness regression（含 TESTING.md §9 HARNESS REVIEW）
PASS（本任务修改 AGENTS/AGENTS 与 PROCESS → PROCESS.md §10 触发 HARNESS REVIEW）：

- [x] 每个文档只有一个主要职责
- [x] 无重大规则重复（AGENTS §3 与 PROCESS §1/§2 的阅读规则指向一致、非重复定义）
- [x] 无互相冲突规则（PROJECT_CONTEXT 冲突条款与 AGENTS §1 一致）
- [x] PROCESS.md 与 AGENTS.md 一致（INTAKE/INSPECTION 条款引用同一索引语义）
- [x] TESTING.md 与 PROCESS.md 一致（未改状态机/Gate，验证契约未受影响）
- [x] PROBLEM.md 是索引而非详细日志（未改）
- [x] docs/problem/ 承载详细记录（未改）
- [x] DECISION.md 记录理由而非流水账（未改）
- [x] ARCHITECTURE.md 反映真实仓库（未改；已知 F8/F9 缺口 → §3 Findings 记录）
- [x] 完成标准可客观验证（V1–V8 清单可复查）
- [x] 自审可以拒绝完成（本报告如任一 V 失败 → 不宣称 PASS）
- [x] 安全敏感改动有更强的验证规则（本次无代码/安全改动，不适用）

### V8 — Diff audit

```text
git diff -- AGENTS.md PROCESS.md PROJECT_CONTEXT.md   → 仅上述 2 处 AGENTS 增强 +
                                                       2 处 PROCESS 增强（见 §5/§6），
                                                       PROJECT_CONTEXT.md 为新增文件
git status  → 本任务文件：AGENTS.md(M) / PROCESS.md(M) / PROJECT_CONTEXT.md(新增) /
              docs/plan/2026-09-22-harness-document-governance-report.md(新增)
              （另有先前任务遗留未提交产物：app/f9/、tests/test_f9_*、F9 spec/plan/batch1 报告等，
              本任务未触碰）
```

范围确认：仅修改本次文档治理授权文件；无生产代码 / Runtime / Agent / API / 数据库 / 依赖 /
前端 / 测试行为改动。

## 11. Remaining Risks

1. PROJECT_CONTEXT 若不按 §12 随 Batch 结束更新会重新过期（缓解：维护规则 + 冲突上报）。
2. 审计发现的 6 项文档问题（§3）未修复——其中 P006 行过期与 TESTING 头部过期会导致低概率
   误读"无测试"；建议后续单独小任务治理（需用户授权）。
3. F8/F9 架构事实未回填 ARCHITECTURE.md，新 Agent 若只读 ARCHITECTURE.md 会漏 F8/F9 边界
   （缓解：PROJECT_CONTEXT §4/§10 显式指向 Freeze Review / F9 Spec；正式回填需单独任务）。
4. 本治理任务本身为一次性 meta 任务，已完成；不构成新的常驻流程负担。

## 12. Final Decision

- 交付：`PROJECT_CONTEXT.md`（新增）；`AGENTS.md`、`PROCESS.md`（最小增强）；本报告。
- 验证：V1–V8 全部 PASS；HARNESS REVIEW 12 项全部 PASS；token/context 策略与职责模型符合目标。
- 结论：

```text
HARNESS DOCUMENT GOVERNANCE — PASS
```

未发现需要停止上报的 Authority 冲突（PROJECT_CONTEXT 与任何权威文档无冲突；冲突条款已按
AGENTS.md §1 预先定义处理路径）。下一步（F9 Batch 2 或其它）等待用户指令。
