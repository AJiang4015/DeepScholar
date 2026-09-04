# P002 — 文件工具会话目录隔离可被绕过

## Status

Mitigated（R1 修复：resolve_path 全分支统一包含性校验，fail-closed，见 Resolution）

## Severity

High

## Created / Last Updated

2026-09-02 / 2026-09-03

## Path

`app/utils/path_utils.py`（`resolve_path`）及其调用方：`app/tools/upload_file_read_tool.py`、`app/tools/markdown_tools.py`、`app/tools/pdf_tools.py`

## Problem

`resolve_path` 的 docstring 声称"尽量把任务产物限制在当前会话目录中"，但实现允许逃逸：

1. **绝对路径分支**（第 45–59 行）：解析后不在 session_dir 内时**原样返回**（第 58–59 行注释："真实绝对路径且不在 session_dir 中时保持原样"）。模型传 `/etc/passwd` 或任意绝对路径即可越过会话目录。
2. **相对路径分支**（第 70 行 `return str(session_path / path)`）：不做最终包含性检查，`../` 可逃出 session_dir。
3. **updated/ 前缀分支**（第 33–36 行）：直接 `Path(relative_part).resolve()`，不经 session_dir 包含性检查。

`resolve_path` 被读文件、写 Markdown、转 PDF 三个工具复用；模型由用户提示驱动，恶意提示可诱导模型读写会话目录外任意路径（包括 `.env` 与 app 源码，取决于进程权限 → 潜在代码执行）。

## Impact

- 任意文件读取（信息泄露）。
- 任意文件写入（覆盖 `.env` / 源码 → 代码执行风险，取决于运行进程权限）。
- 破坏"会话级上下文隔离"这一已声明架构亮点（README「项目亮点」）。

## Root Cause

实现意图与实现不一致：包含性检查只做了一部分，且把"保持外部路径原样"误当作安全行为。见 DECISION.md D002。

## Evidence

- `app/utils/path_utils.py:45-59` 绝对路径直接放行。
- `app/utils/path_utils.py:70` 相对路径无最终包含性检查。
- `app/utils/path_utils.py:33-36` updated/ 分支无包含性检查。
- 调用方：`app/tools/upload_file_read_tool.py:58`、`app/tools/markdown_tools.py:49`、`app/tools/pdf_tools.py:44,52`。

## Scope

### In Scope

- `resolve_path` 最终结果必须包含在 session_dir 内（fail-closed）。

### Out of Scope

- 移除 updated/ 语义（上传文件必须先复制进会话目录，见 main_agent.py 第 70–79 行）。

## Constraints

- 必须保留现有清洗逻辑：重复嵌套 session 名（`_fix_nested_session_path`）、虚拟前缀（/workspace、/mnt/data、/home/user）、`output/` 前缀、`updated/` 前缀、Windows 盘符与 `\` 路径。
- 不得破坏合法读取上传文件的能力（文件名即 basename 传入）。

## Known Failure Modes

- 包含性检查误伤合法路径（Windows 大小写、符号链接、`..` 归一化时序）。
- 检查顺序错误导致先逃逸后检查。
- 只检查前缀而不检查最终 `resolve()` 结果。
- 目录穿越在检查之后才发生（`session_dir / "../x"` 的 resolve 时机）。

## Candidate Solutions

1. 统一策略：所有分支最终 `resolve()` 后校验 session_dir 包含关系，不满足即拒绝（fail-closed）。
2. 白名单：文件工具只接受文件名（不接收路径），目录由运行时注入。
3. 维持现状——**被拒绝**：与声明的隔离意图矛盾。

## Resolution

已修复（Mitigated，R1 / Spec 2026-09-03-R1-file-security）：

- `resolve_path` 重构为"统一出口 + fail-closed"：所有分支（绝对路径、相对路径、虚拟前缀、
  updated/ 前缀、output/ 前缀、session 名去重）最终都经 `resolve()` 后做
  `is_within_directory(session_dir)` 包含性校验（Windows normcase 大小写不敏感）；
  越界抛 `PathSafetyError`（独立异常，区别于普通 IO/业务异常），不再"原样返回外部路径"。
- `updated/` 前缀不再 resolve 到进程 CWD，而是取 basename 映射回 session_dir
  （上传文件执行前已复制进会话目录，读取能力不变）；清洗分支混入 `..` 显式拒绝。
- 三个调用方工具（read_file_content / generate_markdown / convert_md_to_pdf）单独捕获
  `PathSafetyError` 并返回"安全拒绝：..."中文提示，不被宽泛 except 吞并。
- 测试：tests/test_path_utils.py 覆盖 P002 逃逸矩阵（`../`、绝对路径、output/updated/
  前缀变体、虚拟前缀、Windows 盘符/分隔符、空/None）+ 工具层越界拒绝验证。

残余风险：工具 `__main__` 调试块硬编码 `./examples/test_docs` 为会话目录，仅本地调试
（ARCHITECTURE.md §9，不得作为验证证据）；generate_markdown 允许创建会话内任意子目录
（目录膨胀，低优先）。

## Related Decisions

- D002（会话隔离 via ContextVar + session 目录）
- D008（R1 文件安全治理：resolve_path fail-closed + 上传治理 + thread_id 净化）

## Related Architecture

- ARCHITECTURE.md §5（安全边界）、§9（Forbidden Changes）

## Related Tests

- 无（P006）。修复后 MUST 新增 `resolve_path` 单元测试：`../` 逃逸、绝对路径、`updated/` 前缀、重复 session 名、虚拟前缀、Windows 分隔符。

## Remaining Risks

- `generate_markdown` 允许创建 session_dir 下任意子目录（目录爆炸，低优先）。
- 若进程权限足够，写入 app 源码仍可构成代码执行——修复优先级应高于 P004。
- 工具 `__main__` 调试块（markdown_tools.py / upload_file_read_tool.py / pdf_tools.py 底部）硬编码 `./examples/test_docs` 为会话目录，运行时绕过 resolve_path 隔离并写入 examples/——仅限本地调试，不得作为验证证据（ARCHITECTURE.md §9）。
