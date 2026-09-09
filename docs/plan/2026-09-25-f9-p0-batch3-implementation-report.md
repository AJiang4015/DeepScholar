# F9-P0 Batch 3 — Semantic Gap Judge Implementation Report

> 状态：**BATCH 3 PASS / FROZEN**（用户 2026-09-26 正式 Freeze；本批只交付 Semantic Gap Judge；
> 不宣布 F9-P0 COMPLETE）。
> 依据：F9-P0 Spec Rev2 §2.2/§5/§12（R2-1/R2-3）、Plan Rev2 §D2/§D/§A、Batch 3 Readiness
> Review（CONDITIONAL）＋用户裁决 **A1–A5**（2026-09-24）。
> 上游：Batch 1 Projection（PASS/FROZEN）、Batch 2 Gap Detection（PASS/FROZEN）。

## 1. Scope

- In：`app/f9/judge.py` —— 对 Batch 2 deterministic gaps 的 **semantic judgment**（LLM），
  A1–A5 裁决落地；输出 in-memory、bounded、deterministic validation；F8 callback 计费；
  judge failure → 同 F8 execution baseline fallback 语义（调用方接管）。
- Out（未实现）：Follow-up Plan / Query generation / Dedup / Targeted Research / Verification
  orchestration / F7 / Orchestrator / 新 Agent・Runtime・Controller・Task・BudgetCounter /
  migration / 修改 F1–F8・Batch 1・Batch 2・F9 Spec・Plan。

## 2. A1–A5 Decisions（裁决落地）

- A1：Judge 只输出 semantic judgment，每 gap 恰五字段 `gap_id / important / reason / priority /
  semantic_need`；禁止 query/queries/objective/tool·source selection/search strategy/
  dedup_identity/targeted task/verification execution（validator 对未知字段 fail-closed）。
  gap_id = Batch2 signal 的派生稳定 identity `type|subject_type|subject_id`（Batch2 无独立 id 列，
  唯一键派生，deterministic；实现决定并在本报告记录）。
- A2：`signals==[]` → 不调用 LLM（judged=False），交由上层 deterministic no-gap 路径；
  F9 STOPPED 与 F8 terminal 仍为两层（本批不引入任何新 terminal state）。
- A3：无 Judge 专用 retry；provider error / malformed / schema / 缺 required / invalid gap
  reference / validation 失败 = judge failure（丢弃 partial）。F8 control signals
  （GovernanceLimitExceeded / asyncio.CancelledError）**原样传播**，不包装成 JudgeError。
- A4：judge_model 依赖注入（LangChain ChatModel 接口，仅用 ainvoke）；无 provider/model 硬编码；
  无温度协议约束；调用 = `ctx.make_handler()` + `ainvoke(config={"callbacks":[handler]})`。
- A5：结果不持久化（in-memory；无表/migration/replay/crash recovery；research_rounds 后续
  additive Gate 另行裁决）。

## 3. Judge Contract

- 输入：Batch1 Projection（含 round/budget facts）+ Batch2 GapSignals + bounded prompt
  （claim statement ≤400、sq question ≤400、整体 ≤16000 字符确定性截断）。
- 输出：`{round, judged: bool, judgments: [{gap_id, important(bool), reason(≤600),
  priority∈{high,medium,low}, semantic_need(≤1000)}]}`；顺序 = Batch2 signal 顺序；
  completeness = 每个 gap 恰一条（漏判/多判/未知引用均失败）。
- 常量：REASON_MAX=600、SEMANTIC_NEED_MAX=1000、SNIPPET_MAX=400、CONTEXT_CHAR_CAP=16000、
  PRIORITIES={high,medium,low}（Spec 未定义 → 最小 bounded 结构，report 记录）。
- Prompt 明确禁止输出 executable plan/query 类字段（system prompt + validator 双保险）。

## 4. Input Boundary

- 仅 Projection + GapSignals + 显式任务描述；无 parent message history / Agent context /
  tool payload / 无界 evidence·search 内容 / 隐式全局状态 / 直接读 Research DB。
- Judge 无 DB 依赖（纯函数 + model.ainvoke）→ 无 PostgreSQL 集成测试需求（用户 §10 "仅在确有
  DB integration requirement 时"；跨 DB 语义由 Batch1/2 已证管道覆盖）。

## 5. Output Schema（见 §3；validation 链：parse → schema → 引用 → bounds → normalization）

- LLM 文本 → json.loads → wrapper `{"judgments":[...]}` → 逐条字段/类型/枚举/长度 → 未知字段拒
  → 引用集合=输入 gap 集（多/漏/未知/跨 run 一律拒）→ 归一（trim、按输入序）→ valid。
- 任一失败 = JudgeError（partial 不入下游）；无任何自由文本透传。

## 6. LLM / Callback / Budget

- 计费路径（实读 callbacks.py）：judge 直调无 langgraph 元数据 → 冻结分类 `unclassified` →
  只 `acquire_llm_call()`：**+1 llm_calls / +0 agent_steps**（T9 断言 + 实测）。
- `handler = ctx.make_handler()` 单实例复用（测试断言 h1 is h2）。
- 预算耗尽（before/during）：on_llm_start raise `GovernanceLimitExceeded` → judge 不吞 →
  Controller 收敛 budget_exceeded（T6/T6c；model.calls==0 证明 provider 前拦截）。
- 无温度/模型协议约束；真实 provider 受凭据限制 → 测试用 Scripted/Fake ChatModel
  （BaseChatModel 子类，object.__setattr__ 规避 pydantic v2 冻结；与 F3 FakeVerifier 纪律一致）。

## 7. Failure / Fallback

- JudgeError（provider/parse/validation）→ 上层（未来 orchestrator）按 R2-1：丢弃 partial →
  **同一 F8 execute 内** baseline research/synthesis；本批以 seam 测试验证"同 execute 内捕获 →
  baseline 分支 → normal completed"（T11），真实 run_deep_agent baseline + F7 exactly-once
  属 Batch 6/7 seam（本批不越界、不实现 F7）。
- GovernanceLimitExceeded / CancelledError 不包装、不吞并（A3）——T6/T6c/T7/T8 验证终态为
  budget_exceeded / cancelled / timed_out，且均非 completed。

## 8. F8 Governance Seam

- Judge 只运行于 governance-active context（无 ctx → JudgeError）；与主研究共享
  task_id / run_id / GovernanceExecution / BudgetCounter / deadline / cancellation
  （T10：observed identity == TaskRecord.task_id/run_id，counter 同实例，counters_snapshot
  llm_calls==1 / agent_steps==0）。
- 无第二 Controller/Runtime/Task（T11 断言 active handles 空、同 store、同 execute）。

## 9. Tests（24 passed）

- `tests/test_f9_judge.py`：parse 纯函数边界（12）＋ judge_gaps 行为（T1–T6/T9 单元，7）＋
  governance seam（T6c/T7/T8/T10/T11/单 handler，6）。
- T1 valid ✓ / T2 no-gap 不调 LLM ✓ / T3 malformed→JudgeError（llm 已计 1）✓ /
  T4 invalid gap + cross-run 引用→JudgeError ✓ / T5 provider failure→JudgeError ✓ /
  T6 llm 耗尽→GovernanceLimitExceeded（不吞、model 0 调）✓ / T6c policy→budget_exceeded ✓ /
  T7 cancel→cancelled ✓ / T8 timeout→timed_out（llm 已计 1）✓ / T9 +1llm/+0step + 无裸调
  （degraded diagnostic 存在）✓ / T10 同身份 ✓ / T11 同 execute baseline→completed ✓ /
  T12 Batch1/2 回归（下方 Regression）✓。

## 10. AC → Test → Evidence

| AC（用户 §11/§13） | Implementation | Test | Evidence |
|---|---|---|---|
| A1：Judge 只出五字段、无 plan/query 类输出 | judge.py schema + 未知字段 fail-closed | parse 未知字段/枚举/长度/引用测试 | 12 parse 用例全过 |
| A2：无 gap 不调 LLM | judged=False 早退 | T2 | model.calls==0 |
| A3：failure→judge failure、F8 control 不吞 | except 顺序 + 不捕获 GLE/Cancel | T3/T4/T5/T6/T7/T8 | JudgeError/终态断言 |
| A4：DI model + 显式 callback | `_call_model` 只用 ainvoke+handler | T1/T9/T10 + 代码审查 | llm+1/agent0/diag |
| A5：不持久化 | 无 DB import/无写 | 代码审查 + git diff | 无新表 |
| 计费 +1llm/+0step | 冻结分类 unclassified | T9/T10 | counters_snapshot |
| F8 seam 单执行体 | execute(task_id, coroutine, policy) | T6c/T7/T8/T10/T11 | 终态/身份断言 |

## 11. Regression

- sqlite 全量：**645 passed / 85 skipped / 0 failed**（= 621 + Batch3 24；排除 mysql 环境污染
  文件，既有已知项；含 Batch 1/2 FROZEN 测试全绿）。
- PG 双 DSN（research F1–F7 + governance + F9 projection/gaps）：**74 passed / 0 failed**
  （governance 表先 reset）。Batch3 自身无 DB 集成 → 无 PG 测试文件（理由见 §4）。
- ruff / format / compileall（app/f9）全绿。

## 12. Limitations

1. N1（Readiness 已记录）：Judge 经 F8 handler 计费每次产生 `llm_classification_unresolved`
   degraded diagnostic（F8 冻结分类 fail-safe 固有行为）。**F8 FROZEN 不改**；如实记录：
   T1/T9 断言其存在以证明走 handler 路径（非裸调），后续如需静默属 F8 扩展（需另裁）。
2. gap_id 为 Batch2 派生 identity（`type|subject_type|subject_id`）——Batch2 FROZEN 无独立 id
   列；已实现为稳定函数 `gap_id_of`（若未来 Batch2 additive 增加 id 列需另行裁决）。
3. priority/reason/semantic_need 约束为本批最小 bounded 结构（Spec 未定义类型/长度），
   已在 §3 记录；Eval（Batch 8）可校准。
4. T11 的 baseline 为 seam 级验证（同 execute 内捕获→baseline 分支→completed）；真实
   run_deep_agent baseline 回退与 F7 exactly-once 需 Batch 6/7 完成，属预期分层，非缺口。
5. 真实 provider E2E 受凭据限制（fake/scripted ChatModel 验证协议与计费链路，同 F3 纪律）。

## 13. Scope Audit

- 新增：`app/f9/judge.py`、`tests/test_f9_judge.py`、本报告。**未新增 PG 测试**（§4 理由）。
- 零改动：`projection.py`、`gaps.py`、F8（callbacks/context/controller/counters/store）、
  F1–F7、Spec/Plan、API/frontend/依赖/migration、Batch 4+ 实现。
- git diff/status 确认仅上述新增（另为先前任务既有未提交产物，未触碰）。

## 14. Final Decision

```text
BATCH 3 PASS / FROZEN（2026-09-26 用户正式 Freeze）
```

实现与验证证据（A1–A5、测试结果、N1、Limitations、Scope Audit）原样保留，无重写。本批未宣布
F9-P0 IMPLEMENTATION COMPLETE。下一阶段 F9-P0 Batch 4 — Follow-up Plan + Validation + Dedup
为 PENDING：等待用户显式指令，不得自动开始。
