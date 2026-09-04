# Spec: 2026-09-03-R1-file-security（ROADMAP R1 文件安全治理）

> **编号规则（已批准）**：
> - `Pxxx` = PROBLEM.md Problem Registry ID（P002/P003/P004 为本任务修复的目标问题）
> - `Rxx` = 工程化 Roadmap ID（本任务 = **R1**）
> - 后续不得再用 Pxxx 表示工程化阶段。
>
> 状态：**已实施（IMPLEMENTED, 2026-09-03）**。P002/P003/P004 → Mitigated（D008）。
> 批准修正：① 编号 R1；② 随机存储名 + manifest 方案批准，manifest **不得成为文件访问
> 授权边界**（最终访问一律 resolve → normalize → is_relative_to(session_dir)）；③ 工具安全
> 拒绝使用独立异常类型，与普通 IO/业务异常可区分，禁止宽泛 except 吞安全异常。
> 实现报告见 2026-09-03 R1 完成报告（ROADMAP.md §8 状态追踪）。

---

## 1. Problem

当前项目存在三个已登记高危/中危问题 + 一项用户明确要求的上传治理缺失，全部集中在
"LLM 可触达的文件路径/标识符处理"：

1. **P002 文件工具会话目录隔离可被绕过**（`docs/problem/P002`，High）
   `app/utils/path_utils.py:resolve_path` 三处逃逸：
   - 绝对路径分支（L45–59）：解析后不在 session_dir 内时**原样返回**（L58–59 注释"保持原样"）——模型传 `/etc/passwd`、`C:\...` 即越界；
   - 相对路径分支（L70 `return str(session_path / path)`）：无最终包含性检查，`../` 可逃出；
   - `updated/` 前缀分支（L33–36）：直接 `resolve()`，不经 session_dir 包含性校验。
   调用方三个 LLM 工具均复用：`upload_file_read_tool.py:58`、`markdown_tools.py:49`、`pdf_tools.py:44,52`。
   威胁模型：模型由用户提示驱动（含 prompt injection 风险），恶意/失控提示可诱导模型
   读写会话目录外任意路径（`.env`、app 源码 → 信息泄露与潜在代码执行）。
2. **P003 上传接口路径穿越写入**（`docs/problem/P003`，High）
   `app/api/server.py:POST /api/upload`（L145–171）：`file_path = target_dir / file.filename`
   （L165）直接以客户端文件名拼接落盘，`file.filename` 可含 `../`、`..\`、绝对路径。
   无认证（P005 Won't Fix）下放大为未认证远程任意文件写入。
3. **P004 thread_id/session_id 未净化拼入目录**（`docs/problem/P004`，Medium）
   客户端可控 thread_id（`/api/task` body L100、`/api/upload` Form L146、WS 路径 L260）
   未净化直接拼入 `output/session_{id}`（main_agent.py:61）、`updated/session_{id}`
   （server.py:160、main_agent.py:72）→ 目录穿越 + monitor 事件路由污染。
4. **上传治理缺失**（用户工程化目标明确要求，覆盖 README 旧边界声明）
   上传接口无文件类型白名单、无大小限制、文件名不净化、落盘名与客户端名一致。
   注：P003 原将"类型白名单"标为 Out of Scope（基于 README 能力边界），本 Spec 依用户
   最新工程化指令将其纳入范围。

## 2. Goal

将 Agent 文件工具与上传接口的路径/标识符处理收敛为 **fail-closed 的文件沙箱边界**：

- `resolve_path` 任何输入解析后**必须**落在 `session_dir` 内，越界即拒绝（不再原样放行）；
- 上传接口：文件名净化（basename 化 + 非法字符清洗，保留中文/空格/括号）、类型白名单
  （对齐 `read_file_content` 可解析格式）、大小上限、**随机存储名 + 原名元数据映射**
  （落盘名不受客户端控制，Agent 读取仍按原名）；
- `thread_id`/`session_id` 净化（安全字符集 `[A-Za-z0-9_-]`），非法输入拒绝或重生成；
- 三个 LLM 文件工具对越界路径返回中文错误而非抛异常/越界访问；
- 保持对外兼容：工具签名、docstring、HTTP 端点语义、前端上传返回结构不变。

完成判定：P002/P003/P004 负路径测试全绿 + 新增上传治理测试全绿 + 三个工具越界返回错误。

## 3. Non-Goals

- **不**做上传内容安全扫描 / 病毒检测 / 文档内容审查（保持轻量，无重量级平台）；
- **不**引入认证/鉴权（P005 Won't Fix 边界保持不变，不经用户批准不触碰）；
- **不**改动 `read_file_content` / `generate_markdown` / `convert_md_to_pdf` 的工具签名与
  docstring（LangChain 工具对模型的可见接口不变，符合 ARCHITECTURE.md §6）；
- **不**修改前端（`/api/upload` 返回 `files` 原名列表，前端去重逻辑兼容）；
- **不**改动下载/列表接口现有 `resolve()` + `is_relative_to` 防护（已安全，保持）；
- **不**重复 P001（SQL 安全）任何内容；
- 随机存储名方案若导致 main_agent 复制链路改动过大，降级为"净化原名直接落盘 + 最终
  包含性校验"（见 Risks，需在实施前确认，不得静默扩大范围）。

## 4. Current Architecture

### 涉及模块与调用链

```text
前端 UploadPanel ──POST /api/upload(thread_id, files)──► server.py
   ├─ file.filename 未净化 ──► updated/session_{thread_id}/<原名>        (P003)
POST /api/task {query, thread_id} ──► server.py run_task
   └─ thread_id 未净化 ──► asyncio.create_task(run_deep_agent)           (P004)
run_deep_agent(session_id) ── main_agent.py
   ├─ output/session_{session_id} 创建 (L61)                              (P004)
   ├─ updated/session_{id} 文件 copy2 进 session_dir (L71-79)
   └─ astream ──► 子智能体调用工具
主智能体文件工具（resolve_path 复用）:
   read_file_content(filename)     upload_file_read_tool.py:58
   generate_markdown(path, name)   markdown_tools.py:49
   convert_md_to_pdf(md_filename)  pdf_tools.py:44,52
      └─ resolve_path(filename, session_dir=ContextVar)                  (P002)
WS /ws/{thread_id} ──► monitor 按 thread_id 定向推送                      (P004)
```

### 数据流（现状）

上传文件 → `app/updated/session_{id}/<原名>` → run_deep_agent 复制到
`app/output/session_{id}/<原名>` → 模型经工具按**原名**读取（提示词注入工作目录指令，
`main_agent.py` path_instruction 要求相对路径）。因此"落盘名"与"模型可见名"目前是同一
名字——任何改名方案都必须维持"模型按原名读"这条链路。

### Agent / Tool / Runtime 交互

Runtime（run_deep_agent）负责建目录 + 注入工作目录指令；Tool 通过 ContextVar 拿
session_dir 再 resolve_path。安全缺口在 Tool 的路径解析层与 Runtime 的目录名构造层。

## 5. Proposed Design

### 5.1 `app/utils/path_utils.py` — resolve_path 收紧（修 P002）

保留现有清洗逻辑（虚拟前缀剥离 /workspace /mnt/data /home/user、重复嵌套 session 名修复、
output/ 前缀、updated/ 前缀、Windows 分隔符处理），在其后增加**统一最终包含性校验**：

```text
所有分支产出 candidate 路径
  → candidate 规范化（resolve，处理 .. 与符号链接）
  → 校验 resolve 结果 is_relative_to(session_dir_resolved)（Windows 下大小写不敏感比较）
  → 越界：raise ValueError（fail-closed），由调用方工具捕获转中文错误返回 LLM
```

`updated/` 前缀语义微调：不再解析到进程 CWD，而是取 basename 映射回 `session_dir` 内
同名文件（上传文件已由 run_deep_agent 复制进 session_dir，保证包含性且读取能力不变）。

错误信息只含通用原因 + 输入文件名，不含内部路径/堆栈。

### 5.2 `app/utils/session_id.py`（新，纯函数，修 P004）

```python
SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

def is_safe_thread_id(value) -> bool          # 前端 uuid4 / manual-* 格式均兼容
def sanitize_thread_id(value) -> str          # 非法则抛 ValueError（供 upload/WS 用）
def safe_thread_id_or_new(value) -> str       # 非法或 None 则生成 uuid4().hex（供 /api/task 用，前端接受返回的 thread_id 并 storeThreadId）
```

server.py 接入点：`/api/task`（非法 → 重生成，兼容前端）、`/api/upload` Form thread_id
与 `/ws/{thread_id}`（非法 → 400 / 连接关闭，避免目录穿越与事件路由污染）。

### 5.3 `app/utils/upload_guard.py`（新，纯函数，上传治理）

```python
ALLOWED_UPLOAD_EXTENSIONS = {".md", ".txt", ".docx", ".pdf", ".xlsx", ".xls"}
    # 与 upload_file_read_tool.py 可解析分支一致，避免"能上传不能读"
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024   # 可经 env 覆盖

def sanitize_filename(raw: str) -> str
    # basename 化；拒绝/清洗 / \ .. 控制字符；保留中文/空格/括号/._-；
    # 长度上限（如 255）；空/纯分隔符/纯 .. 抛 ValueError
def validate_extension(name, allowed=None) -> None        # 不匹配抛 ValueError
def validate_size(size_bytes, max_bytes=None) -> None     # 超限抛 ValueError
def generate_storage_name(original: str) -> str           # uuid4().hex + 保留小写扩展名（不含原名）
def load_upload_manifest(dir) -> dict[str, dict]          # {original: {storage, size, uploaded_at}}
def save_upload_manifest(dir, manifest) -> None           # 原子写（tmp + rename）
```

### 5.4 `app/api/server.py` — upload 端点改造（修 P003 + 上传治理）

```text
POST /api/upload
  1) thread_id = sanitize_thread_id(Form)         # P004（非法 → 400）
  2) 对每个 UploadFile：
       original = sanitize_filename(f.filename)    # P003（穿越/控制字符拒绝）
       validate_extension(original)                # 类型白名单
       storage = generate_storage_name(original)
       流式写入 updated/session_{id}/{storage}，边写边累计字节，超限即中止删除（防磁盘耗尽）
       manifest[original] = {storage, size, uploaded_at}
  3) save_upload_manifest(...)；返回 {"status":"uploaded","files":[原名...]}   # 前端兼容
```

### 5.5 `app/agent/main_agent.py` — 复制逻辑按 manifest 还原原名

run_deep_agent 复制段（现 L71–79）改为：读取 `updated/session_{id}/.manifest.json`，
按 `manifest[original].storage` 把存储名文件复制为 `session_dir/{original}`；
无 manifest 时回退旧行为（原样复制，兼容旧会话）。跳过 manifest 文件本身。
模型可见名、read_file_content 的 filename 语义不变。

> **安全不变量（已批准）**：manifest 只是"原名 ↔ 存储名"的名称映射，**绝不构成文件访问
> 授权边界**。无论 manifest 内容如何被篡改，工具层对任何文件的最终访问都必须经过
> `resolve_path` 的 `resolve → normalize/resolve → is_relative_to(session_dir) → allow/reject`
> 链。即：篡改 manifest 只能影响"复制时用哪个原名展示"，**不能突破 session sandbox**
> 读取/写入会话目录之外的文件。该不变量由 5.1 的统一包含性校验保证，与 manifest 解耦。

### 5.6 三个文件工具 — 越界错误处理（异常分层，安全拒绝可区分）

新增独立异常类型 `PathSafetyError`（`path_utils.py` 内定义，继承 `ValueError`），
由 `resolve_path` 在越界时抛出。三个工具按**精确异常类型**捕获并转中文安全拒绝，
禁止用宽泛 `except Exception` 吞掉安全异常：

```text
LLM tool call → resolve_path → security validation（越界 → raise PathSafetyError）
             → 工具层 except PathSafetyError → 返回中文安全拒绝
               （如 "安全拒绝：路径不在当前会话目录范围内，已拒绝访问"）
普通 IO/业务异常 → 各自原有 except 分支（"读取文件出错/生成失败/转换失败"）
```

安全拒绝与普通 IO/业务错误**保持可区分**（不同异常类型 → 不同用户消息），
便于模型识别"这是安全边界"而非"重试即可"。`pdf_tools.py` 需把 PathSafetyError 的
except 放在通用 Exception 之前或单独捕获，避免被外层吞并。

### 5.7 `.gitignore`

追加 `pytest-cache-files-*/`（本环境沙箱遗留目录，gitignore 后消除 git 遍历噪音）。

### 5.8 与现有 Agent 架构的关系

不触碰 Orchestrator/子智能体/WS schema/HTTP 语义；改动集中在 Runtime 目录构造、
Tool 路径解析层、上传端点——均为 Agent 安全边界（Tier 4）而非业务流程。

## 6. Files To Change

### New

- `app/utils/upload_guard.py`
- `app/utils/session_id.py`
- `tests/test_upload_guard.py`
- `tests/test_session_id.py`
- `tests/test_path_utils.py`
- `docs/spec/2026-09-03-P002-file-security.md`（本文件）

### Modify

- `app/utils/path_utils.py`（resolve_path 最终包含性校验，fail-closed）
- `app/api/server.py`（upload 治理 + thread_id 净化接入）
- `app/agent/main_agent.py`（复制逻辑按 manifest 还原原名；目录名基于净化后 id）
- `app/tools/upload_file_read_tool.py`（resolve 越界 → 中文错误）
- `app/tools/markdown_tools.py`（resolve 越界 → 中文错误）
- `app/tools/pdf_tools.py`（确认越界错误路径已覆盖）
- `.gitignore`

### Delete

- 无

范围纪律：以上文件若实施中发现必须超出，立即停止并回改本 Spec 再等批准，不自行扩大。

## 7. Agent Value

> 这个改动为什么属于 Agent Engineering，而不是普通后端上传安全？

威胁模型不同：普通上传安全的对手是"直接提交恶意请求的客户端"；这里的对手是
**"由不可信指令（用户提示 + prompt injection）驱动的半自主执行体（LLM + 工具）"**。
模型会自己构造路径参数调用工具——因此防御必须落在**工具层的 fail-closed 路径沙箱**
而不是信任模型"守规矩"。这正是 Agent Security / Tool Governance / Guardrails 的核心：

- **Tool Permission**：工具只能操作授权工作区（session_dir），与 SQL 白名单（P001）同构；
- **Data Access Boundary**：prompt injection 试图诱导模型读 `.env`/源码时，工具层拦截；
- **Audit-ready**：上传原名↔随机存储名映射 = 可审计的存储模型；
- 与 P001 形成同一叙事线："**把 LLM 触达的每个副作用面（SQL、文件系统、会话标识）
  都收敛到显式边界 + 负路径测试**"——这是 Agent 面试中极强的一体化工程主张。

## 8. Interview Value

可形成的 Agent 面试话题：

- 为什么 LLM 文件工具必须做路径沙箱？prompt injection 如何放大为任意文件读写？
- fail-closed vs fail-open 在工具层的取舍；为什么"保持外部路径原样"是伪安全？
- 为什么上传落盘名要与模型可见名解耦（随机存储名 + 原名映射）？
- 路径包含性校验的坑：`resolve()` 时机、`..` 归一化、Windows 大小写/分隔符、符号链接；
- 与 P001 SQL 白名单的统一模式：工具安全 = 类型校验 + 访问范围白名单 + 审计；
- 为什么 thread_id 这种"标识符"也必须净化（目录穿越 + 事件路由污染两个攻击面）。

## 9. Test Plan

纯函数层（本环境可全量运行，pytest）：

- `test_path_utils.py`：
  - 正常：文件名、子目录相对路径、虚拟前缀（/workspace/output/session_x/x.md）、
    Windows 分隔符、嵌套 session 名去重、session 内绝对路径；
  - 逃逸负路径（P002 矩阵）：`../../x`、绝对路径 `/etc/passwd`、`C:\windows\x`、
    `updated/` 变体、`output/../..`、空串/None/超长；
  - 断言：越界一律抛 ValueError（fail-closed），合法路径不被误伤；
- `test_upload_guard.py`：类型白名单允许/拒绝；大小超限；文件名穿越矩阵
  （`../x`、`..\x`、绝对路径、空名、纯 `.`/`..`、控制字符、超长）；中文/空格/括号名保留；
  存储名格式（uuid + 扩展名，不含原名）；manifest 保存/加载往返；
- `test_session_id.py`：uuid4/manual-* 通过；`/`、`\`、`..`、空串、超长、控制字符拒绝；
  `safe_thread_id_or_new` 非法输入生成合法新 id。

工具层（本环境可 import 三个工具模块，langchain_core/fastapi 可用）：
- 三个工具对越界 filename 返回中文错误而非抛异常（monkeypatch session context）。

回归：现有 `tests/test_db_tools.py` 86 项保持全绿（不触碰 SQL 面）。

手动 E2E（需完整依赖环境，由用户在批准后/实施后执行，或本环境具备时执行）：
上传恶意 filename → 400；上传正常中文文件 → 任务内按原名可读 → 文件可下载；
重启后 updated/output 目录结构正确。server.py 无法在本环境 import（缺 deepagents），
如实报告为验证缺口，不伪造 TestClient 结果。

## 10. Risks / Limitations

- **随机存储名改变 upload→session_dir 复制链路**：依赖 manifest 严格同步；manifest 缺失
  或损坏时回退策略需明确（复制跳过随机文件并告警，避免静默丢文件）。若实现中发现
  复杂度超预期，降级方案见 Non-Goals（净化原名直接落盘 + 最终包含性校验），需用户确认。
- `resolve_path` 收紧后，模型历史上"绝对路径写文件"的（本应禁止的）行为将失败——
  提示词已引导相对路径，风险可控；但需在 E2E 验证任务链路不回归。
- 本环境无法 import `app.api.server`/`main_agent`（缺 deepagents/mysql），server 薄层
  与复制链路的集成验证依赖用户完整环境；本 Spec 将可测逻辑全部下沉纯函数以最大化本地验证。
- Windows 符号链接与大小写边界：包含性校验基于 resolve 后路径，符号链接逃逸按 fail-closed
  拒绝；大小写用 normcase 比较，跨平台行为需在 Linux 上复核（教学场景低风险）。
- 类型白名单仅按扩展名，不做 MIME/魔数校验（防伪装上传仍需内容级检查——已声明 Non-Goal）。

## 11. Resume Claims

### 可以写（严格对应最终实现）

- 为 LLM 文件工具实现 fail-closed 路径沙箱：统一路径规范化 + 会话目录包含性校验，
  阻断 `../`/绝对路径/符号链接逃逸，修复已登记的任意文件读写问题（P002）；
- 上传治理：类型白名单、大小上限、随机存储名 + 原名映射（防路径穿越与落盘名注入，P003）；
- 会话标识净化：thread_id 安全字符集校验，修复目录穿越与事件路由污染（P004）；
- 三个注册高危/中危问题修复 + 全量负路径对抗测试（pytest，含逃逸矩阵）；
- 与 P001 同模式的 Agent 工具安全叙事：SQL 白名单 + 文件沙箱 = "LLM 副作用面收敛到显式边界"。

### 不能写

- "完整文件安全平台 / 病毒扫描 / 内容审查"（未实现）；
- "生产级部署 / 高并发上传"（无压测、无集群）；
- 任何依赖本环境未验证的集成行为（server/main_agent 集成需完整环境 E2E 后才可声称）。

---

## 审核提示（给用户）

1. **命名体系**：采用 P002（本文件当前名）还是切回 ROADMAP R1 编号？（决定是否重命名）
2. **随机存储名方案**：接受 manifest 复制链路改造（5.5），还是采用降级方案（净化原名直接落盘）？
3. **审核重点**：Problem 引用是否与代码一致、Non-Goals 边界、Files To Change 是否越界、
   测试计划是否覆盖 P002/P003/P004 负路径矩阵。
