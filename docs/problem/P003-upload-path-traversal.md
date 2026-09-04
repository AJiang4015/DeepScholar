# P003 — 上传接口未净化文件名，存在路径穿越写入

## Status

Mitigated（R1 修复：文件名净化 + 类型/大小白名单 + 随机存储名 + manifest，见 Resolution）

## Severity

High

## Created / Last Updated

2026-09-02 / 2026-09-03

## Path

`app/api/server.py`（`POST /api/upload`）

## Problem

`POST /api/upload`（第 145–171 行）将客户端提供的 `file.filename` 直接拼入目标路径：
`file_path = target_dir / file.filename`（第 165 行），并以 `"wb"` 打开写入（第 167 行）。
`file.filename` 可含 `../` 或 `..\`，导致写入位置逃出 `updated/session_{thread_id}`，实现任意路径文件写入。
该接口无认证（P005）、无文件大小/类型限制。

## Impact

- 任意（未认证）客户端可写任意路径文件：覆盖 `.env`、app 源码等 → 代码执行风险，取决于进程权限。
- 结合 P005（无认证）放大为未认证的远程文件写入。

## Root Cause

上传路径未做 `resolve()` + 包含性校验；`file.filename` 直接作为路径组件使用，未取 basename。

## Evidence

- `app/api/server.py:165` `file_path = target_dir / file.filename`
- `app/api/server.py:167-168` `file_path.open("wb")` 写入
- `app/api/server.py:160` `target_dir = updated_dir / f"session_{thread_id}"`
- 对照：`/api/download` 与 `/api/files` 已有 `resolve()` + `is_relative_to` 检查（第 186–194、218–229 行），上传缺少同等防护。

## Scope

### In Scope

- 上传文件名净化（basename 化并拒绝路径分隔符），或 resolve 后包含性校验。

### Out of Scope

- 文件类型 / 内容安全扫描（README 能力边界已声明不在范围）。

## Constraints

- 上传后文件名须能在 main_agent.py 第 75–79 行复制进会话目录时保持可用；**中文文件名必须支持**。
- 同一会话重复上传同名文件应可覆盖（教学交互需要）。

## Known Failure Modes

- 只替换 `\` 不替换 `/`（或反之）。
- Unicode 变体 / 编码绕过（低风险，教学场景）。
- 误杀合法中文、空格、括号文件名。

## Candidate Solutions

1. `Path(file.filename).name` 取 basename（丢弃一切目录部分）——最小修改。
2. resolve 后校验位于 `updated_dir` 内。
3. 服务端生成存储名（uuid）+ 维护原名映射——改动最大，需 Decision。

## Resolution

已修复（Mitigated，R1 / Spec 2026-09-03-R1-file-security；覆盖范围超出原 P003，含用户工程化指令要求的上传治理）：

- `/api/upload` 重写（server.py）：
  1. thread_id 净化（P004，安全字符集，非法 400）；
  2. `sanitize_filename`：basename 化 + 控制字符/Windows 非法字符清洗，拒绝空名/`.`/`..`/超长；
     中文、空格、括号等合法字符保留（app/utils/upload_guard.py，纯函数）；
  3. `validate_extension` 类型白名单：.md/.txt/.docx/.pdf/.xlsx/.xls（与 read_file_content 对齐）；
  4. `validate_size` 大小上限（默认 20MB，UPLOAD_MAX_BYTES 可覆盖），流式分块写入边写边累计；
  5. **随机存储名**（uuid4().hex + 小写扩展名）落盘，原名写入 `.manifest.json` 映射
     （仅名称映射，非授权边界——文件访问一律经 resolve_path 的 session 包含性校验）；
  6. 同名重复上传覆盖旧文件（教学交互语义保留），半写失败自动清理。
- `run_deep_agent` 复制段按 manifest 还原为"原名"复制进会话目录（无 manifest 时回退旧行为），
  模型按原名读取语义不变（main_agent.py）。
- 测试：tests/test_upload_guard.py（穿越矩阵、白名单、manifest 往返、篡改防御）+
  test_session_id.py；main_agent/server 集成依赖完整环境（deepagents 缺失），如实列为验证缺口。

残余风险：仅按扩展名白名单，无 MIME/魔数内容校验（内容安全扫描为明确 Non-Goal）；
上传无认证（P005 Won't Fix 边界未变）。

## Related Decisions

- D002（会话目录隔离）
- D003（教学边界：无认证）
- D008（R1 文件安全治理）

## Related Architecture

- ARCHITECTURE.md §5（安全边界）、§9（Forbidden Changes）

## Related Tests

- 无（P006）。修复后 MUST 新增上传路径穿越的接口测试（FastAPI TestClient）。

## Remaining Risks

- 若未来引入认证（改变 P005 边界），攻击面需重新评估。
- 上传文件本身仍是用户可控内容，读取链（read_file_content）无内容审查（已声明边界）。
- 上传接口无文件大小/数量限制，可被用于磁盘耗尽（Low；教学边界内未声明防护）。
