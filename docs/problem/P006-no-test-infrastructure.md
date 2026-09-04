# P006 — 无自动化测试基础设施

## Status

Open

## Severity

Medium

## Created / Last Updated

2026-09-02 / 2026-09-02

## Path

仓库根（缺失的 tests/、pyproject.toml、frontend/package.json、.pre-commit-config.yaml）

## Problem

仓库没有任何自动化测试：

- 不存在 tests/ 目录（README.md 第 161 行项目结构中声称 "tests/ # 测试目录"，与实际不符）。
- pyproject.toml 无 pytest 或任何测试依赖 / 配置。
- 前端 package.json 无 test 脚本（仅 dev / build / preview）。
- .pre-commit-config.yaml 只有 lint/format（trailing-whitespace、end-of-file-fixer、check-yaml、check-toml、ruff check --fix、ruff format）。
- 现有验证手段仅为：pre-commit / ruff（但 ruff 未声明，见下）、`uv sync --frozen`、compileall、前端 `pnpm build`、手动 E2E；模块 `__main__` 调试块仅为本地调试入口，不得作为验证证据。

## Impact

- TESTING.md 的验证契约无法落地为自动化证据；回归风险无防护。
- Agent 按 README 找 tests/ 会扑空（重复工程成本）。
- 安全问题（P001–P004）修复后无法建立回归防线。

## Root Cause

教学项目演进过程中未引入测试；README 项目结构图超前于实现。

## Evidence

- `README.md:161` 声称 tests/ 存在。
- `pyproject.toml` 无 dev/test 依赖与配置。
- `frontend/package.json` scripts 仅 dev/build/preview。
- `.pre-commit-config.yaml` 无测试 hook。
- 全仓库 grep：无 pytest / test_ 用例文件。
- ruff 在 .pre-commit-config.yaml 中被 `uv run --frozen ruff ...` 调用，但 ruff 未在 pyproject.toml / uv.lock / requirements.txt 中声明（grep 0 命中）——干净环境下 lint 验证不可用。
- requirements.txt 与 pyproject.toml 双份依赖清单并存，无一致性验证手段（漂移风险）。

## Scope

### In Scope

- 引入 pytest 作为后端测试框架（需走 Architecture Change Gate：新增核心依赖 → Decision，见 D006）。
- 修正 README 结构图中不存在的 tests/ 声明（最小文档修正）。

### Out of Scope

- 为所有功能补全测试（仅安全敏感代码为 MUST，见 TESTING.md §5）。

## Constraints

- 引入 pytest 不得影响 `uv sync --frozen` 可复现性（作为 dev dependency 声明，更新 pyproject.toml + uv.lock）。
- 测试不得依赖外部服务在线（LLM / Tavily / RAGFlow 需 mock 或 skip）；MySQL 测试用 docker compose 本地库。

## Known Failure Modes

- 测试依赖真实 LLM/Tavily/RAGFlow 导致不稳定（必须 mock 或标记 skip）。
- 为"测"而测，产生无断言的空测试。
- 引入 pytest 时顺带升级其它依赖（违反最小修改）。

## Candidate Solutions

1. 引入 pytest + httpx（FastAPI TestClient 依赖），新增 tests/，更新 TESTING.md §1。
2. 维持现状（手动验证）——**被拒绝**：无法满足证据契约。
3. 仅修 README 结构图（去掉 tests/ 声明）——作为独立的最小文档修正，不替代方案 1。

## Resolution

未解决。任何安全敏感改动（P001–P004 相关）必须同步引入 pytest 测试（TESTING.md §5、DECISION.md D006）。

## Related Decisions

- D006（测试策略决策）

## Related Architecture

- ARCHITECTURE.md §8（Architecture Change Gate：新增核心依赖）

## Related Tests

- 本问题即测试基础设施本身；解决后 TESTING.md §1 验证命令需更新。

## Remaining Risks

- 引入测试框架本身是一次核心依赖变更，需用户在 Decision 中确认后才可执行。
