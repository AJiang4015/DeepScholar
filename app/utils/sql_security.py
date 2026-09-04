"""
SQL 只读安全校验工具（Agent Text-to-SQL 最小只读访问边界）

背景：LLM 生成的 SQL 会进入 db_tools.execute_sql_query 执行。本模块为工具层
提供第一道防线，把「只读 + 表名白名单」约束落在代码里，而不是依赖提示词自觉
（P001：教程版为零约束）。

本模块是纯函数实现，不依赖数据库驱动 / LangChain / FastAPI，便于单元测试。
安全链路（由 db_tools.py 按序调用）：

    execute_sql_query
        -> validate_read_only_sql   （语句类型 + 单语句 + 无副作用导出）
        -> extract_table_names      （按关键字位置提取 FROM/JOIN/DESC 引用的表）
        -> validate_allowed_tables  （与允许表集合比对，未授权即拒绝）
        -> validate_params          （只校验容器类型，绝不把值拼进 SQL）
        -> 数据库驱动参数绑定执行   （cursor.execute(sql, params)，在 db_tools.py）
        -> 审计日志                 （在 db_tools.py，不记录参数值）

明确的边界（本层不是完整 SQL Parser，也绝不声称"防 SQL 注入"）：
- 语句类型仅允许：SELECT / SHOW / DESC / DESCRIBE / EXPLAIN（大小写不敏感）。
- 只允许单条语句；末尾单个分号容忍，语句中间出现分号即拒绝。
- 词法器会跳过字符串字面量与注释（含 --、#、/* */ 与 MySQL 可执行版本化注释
  /*! ... */），因此字符串/注释内的分号与关键字不会误判。
- 已知限制（fail-closed，宁拒勿放）：
  1. WITH 前缀的 CTE、FROM 子查询（派生表）不支持，直接拒绝；
  2. Unicode 标识符、方言特性（LATERAL、表函数等）不在支持范围；
  3. 表名提取是词法级启发式，无法保证 100% 覆盖所有合法 SQL 写法；
     因此本层是纵深防御的一层，部署层的"数据库只读账号"仍是推荐的补充防线。
  4. 双引号内容按"可能是标识符"处理（兼容 ANSI_QUOTES 会话下 FROM "table" 的
     写法）；sql_mode=TRADITIONAL（本项目默认）下双引号是字符串，同样安全。
"""

import re
from typing import Any, Optional

# 允许的只读语句类型（语句首个有效关键字，大小写不敏感）
READ_ONLY_KEYWORDS = frozenset({"SELECT", "SHOW", "DESC", "DESCRIBE", "EXPLAIN"})

# 用于报错提示的常见写语句关键字（不影响判定逻辑，判定只认 READ_ONLY_KEYWORDS）
_KNOWN_WRITE_KEYWORDS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "GRANT",
        "REVOKE",
        "REPLACE",
        "CALL",
        "SET",
        "USE",
        "LOCK",
        "UNLOCK",
        "RENAME",
        "LOAD",
        "DO",
        "WITH",
        "MERGE",
        "TRUNCATE",
    }
)

# 会触发表名提取的关键字
_TABLE_TRIGGERS = frozenset({"FROM", "JOIN", "DESC", "DESCRIBE"})

# 见到这些关键字说明 FROM 列表已结束（用于逗号分隔表列表的续接判断）
# 注意：AS 不能在此集合中——FROM t1 AS x, t2 的别名之后仍是表列表，
# 误清会漏检逗号后的第二个表（白名单绕过）。别名只是单个词，留着 in_from_list
# 即可被后续的 WHERE/GROUP/ORDER 等真正结束符复位。
_FROM_LIST_END = frozenset(
    {
        "WHERE",
        "GROUP",
        "ORDER",
        "HAVING",
        "LIMIT",
        "UNION",
        "OFFSET",
        "FOR",
        "ON",
        "USING",
        "SET",
        "INNER",
        "LEFT",
        "RIGHT",
        "CROSS",
        "NATURAL",
        "STRAIGHT_JOIN",
        "QUALIFY",
        "WINDOW",
    }
)

_SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SqlValidationError(ValueError):
    """SQL 校验失败。fail-closed：任何校验失败都拒绝执行，不返回给数据库。"""


# ---------------------------------------------------------------------------
# 词法器：把 SQL 切成 (kind, value) 记号流，跳过注释与字符串字面量
# kind: WORD（标识符/关键字/数字）、QID（`反引号` 或 "双引号" 内容）、PUNCT（单字符）
# ---------------------------------------------------------------------------
def _tokenize(sql: str) -> list:
    tokens: list = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch.isspace():
            i += 1
            continue
        # 行注释：--（需后随空白/行尾，符合 MySQL 规则）与 #
        if sql.startswith("--", i) and (i + 2 >= n or sql[i + 2].isspace()):
            nl = sql.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        if ch == "#":
            nl = sql.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        # 块注释（含 /*! ... */ 版本化注释：一并剥除，防可执行注释载荷）
        if sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        # 字符串字面量：'...' 跳过；"..." 按可能是标识符处理（防 ANSI_QUOTES 下漏检）
        if ch in ("'", '"'):
            quote = ch
            start = i + 1
            i += 1
            while i < n:
                if sql[i] == "\\":  # 反斜杠转义
                    i += 2
                    continue
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:  # 双写引号转义
                        i += 2
                        continue
                    break
                i += 1
            if quote == '"':
                # 双引号内容作为候选标识符（QID）：FROM "table"（ANSI_QUOTES）可被
                # 表名提取捕获；sql_mode=TRADITIONAL（本项目默认）下双引号是字符串，
                # 该记号不处于表名位置时会被忽略，不会造成误判。
                tokens.append(("QID", sql[start:i]))
            # 说明：单引号字符串直接丢弃（值不参与关键字/表名判断）
            i += 1
            continue
        # 反引号标识符
        if ch == "`":
            end = sql.find("`", i + 1)
            content = sql[i + 1 : end] if end != -1 else sql[i + 1 :]
            tokens.append(("QID", content))
            i = n if end == -1 else end + 1
            continue
        # 标识符 / 数字（关键字、表名、列名、字面数字等统一按 WORD 处理）
        if ch.isalpha() or ch == "_" or ch.isdigit():
            j = i
            while j < n and (sql[j].isalnum() or sql[j] in "_$"):
                j += 1
            tokens.append(("WORD", sql[i:j]))
            i = j
            continue
        tokens.append(("PUNCT", ch))
        i += 1
    return tokens


# ---------------------------------------------------------------------------
# 1) 只读语句校验
# ---------------------------------------------------------------------------
def validate_read_only_sql(sql: Any) -> str:
    """
    校验 SQL 是否为「单条只读语句」，通过则返回规范化后的语句类型关键字。

    处理：前导空白 / 前导注释 / 大小写变化 / 末尾单个分号 / 多语句 / 写语句 /
    SELECT ... INTO OUTFILE|DUMPFILE（服务器端文件写入副作用）。
    不通过一律抛 SqlValidationError（fail-closed）。
    """
    if not isinstance(sql, str) or not sql.strip():
        raise SqlValidationError("SQL 为空或不是合法文本。")
    tokens = _tokenize(sql)
    if not tokens:
        raise SqlValidationError("SQL 为空或只包含注释/空白。")
    # 容忍语句末尾单个分号（MySQL 客户端习惯），再多的分号视为多语句
    if tokens[-1] == ("PUNCT", ";"):
        tokens = tokens[:-1]
    if any(tok == ("PUNCT", ";") for tok in tokens):
        raise SqlValidationError(
            "检测到多条语句（以分号分隔），仅允许执行单条只读查询。"
        )
    if tokens[0][0] != "WORD":
        raise SqlValidationError(
            "SQL 必须以关键字开头（SELECT/SHOW/DESC/DESCRIBE/EXPLAIN）。"
        )
    keyword = tokens[0][1].upper()
    if keyword not in READ_ONLY_KEYWORDS:
        if keyword in _KNOWN_WRITE_KEYWORDS:
            raise SqlValidationError(
                f"检测到不允许的写语句类型 '{keyword}'，仅允许只读查询"
                "（SELECT/SHOW/DESC/DESCRIBE/EXPLAIN）。"
            )
        raise SqlValidationError(
            f"不支持的语句类型 '{keyword}'，仅允许只读查询"
            "（SELECT/SHOW/DESC/DESCRIBE/EXPLAIN）。"
        )
    # SELECT ... INTO OUTFILE/DUMPFILE 会把数据写入服务器文件系统，一并拒绝
    if keyword == "SELECT":
        for idx in range(len(tokens) - 1):
            kind, val = tokens[idx]
            if kind == "WORD" and val.upper() == "INTO":
                nxt_kind, nxt_val = tokens[idx + 1]
                if nxt_kind == "WORD" and nxt_val.upper() in ("OUTFILE", "DUMPFILE"):
                    raise SqlValidationError(
                        "不允许 SELECT ... INTO OUTFILE/DUMPFILE（会向服务器文件系统写入）。"
                    )
    return keyword


# ---------------------------------------------------------------------------
# 2) 表名提取（词法级启发式，覆盖 FROM / JOIN / DESC / 逗号表列表 / WHERE 子查询）
# ---------------------------------------------------------------------------
def extract_table_names(sql: str) -> list:
    """
    提取 SQL 中 FROM / JOIN / DESC / DESCRIBE 位置引用的表名。

    覆盖：普通表、JOIN、逗号分隔表列表、WHERE/HAVING 内子查询的 FROM、
    反引号/双引号表名、schema.table 限定名（取最后一段做白名单比对）。
    fail-closed：FROM/JOIN 后紧跟 '('（派生表）或无法识别的记号时抛错。
    已知限制：WITH CTE（关键字层已拒绝）、LATERAL/表函数等方言不在支持范围。
    """
    tokens = _tokenize(sql)
    names: list = []
    expect_table = False  # 下一个记号应当是表名
    in_from_list = False  # 处于 FROM 的逗号分隔表列表中
    i, n = 0, len(tokens)
    while i < n:
        kind, val = tokens[i]
        if kind == "WORD":
            up = val.upper()
            if up in _TABLE_TRIGGERS:
                expect_table = True
                in_from_list = up == "FROM"
                i += 1
                continue
            if expect_table:
                # 表名位置紧跟 '(' -> 表函数/函数调用（如 JSON_TABLE(...)），
                # 本层不支持，fail-closed 拒绝而不是误当普通表名
                if i + 1 < n and tokens[i + 1] == ("PUNCT", "("):
                    raise SqlValidationError(
                        "不支持表函数/派生表写法（FROM 后不允许函数调用），"
                        "请改用直接表名或 JOIN。"
                    )
                # schema.table 限定名：取最后一段做白名单比对
                if (
                    i + 2 < n
                    and tokens[i + 1] == ("PUNCT", ".")
                    and tokens[i + 2][0] in ("WORD", "QID")
                ):
                    names.append(tokens[i + 2][1])
                    i += 3
                    # schema.func(...) 同样是函数调用，一并拒绝
                    if i < n and tokens[i] == ("PUNCT", "("):
                        raise SqlValidationError(
                            "不支持表函数/派生表写法（FROM 后不允许函数调用），"
                            "请改用直接表名或 JOIN。"
                        )
                else:
                    names.append(val)
                    i += 1
                expect_table = False
                continue
            if up in _FROM_LIST_END:
                in_from_list = False
            i += 1
            continue
        if kind == "QID":
            if expect_table:
                if i + 1 < n and tokens[i + 1] == ("PUNCT", "("):
                    raise SqlValidationError(
                        "不支持表函数/派生表写法（FROM 后不允许函数调用），"
                        "请改用直接表名或 JOIN。"
                    )
                if (
                    i + 2 < n
                    and tokens[i + 1] == ("PUNCT", ".")
                    and tokens[i + 2][0] == "QID"
                ):
                    names.append(tokens[i + 2][1])
                    i += 3
                    if i < n and tokens[i] == ("PUNCT", "("):
                        raise SqlValidationError(
                            "不支持表函数/派生表写法（FROM 后不允许函数调用），"
                            "请改用直接表名或 JOIN。"
                        )
                else:
                    names.append(val)
                    i += 1
                expect_table = False
                continue
            i += 1
            continue
        # PUNCT
        if expect_table:
            if val == "(":
                raise SqlValidationError(
                    "不支持 FROM 子查询（派生表），请改用 JOIN 或直接引用表名。"
                )
            if val == ",":
                i += 1
                continue
            raise SqlValidationError(
                f"无法识别的表引用（FROM/JOIN 后遇到 '{val}'），已拒绝执行。"
            )
        if val == "(":
            in_from_list = False
        elif val == "," and in_from_list:
            expect_table = True
            i += 1
            continue
        i += 1
        continue
    return names


# ---------------------------------------------------------------------------
# 3) 表名白名单比对
# ---------------------------------------------------------------------------
def validate_allowed_tables(tables, allowed_tables) -> None:
    """
    比对提取出的表名与允许访问的表集合。发现未授权表立即抛 SqlValidationError。

    :param tables: extract_table_names 的返回（可为空，如 SELECT 1）
    :param allowed_tables: 允许访问的表名集合（来自 list_sql_tables 同一数据源）
    """
    # 去重且保持首次出现顺序
    unique = list(dict.fromkeys(tables))
    if not unique:
        return
    unauthorized = sorted({t for t in unique if t not in allowed_tables})
    if unauthorized:
        allowed_txt = (
            "、".join(sorted(allowed_tables))
            if allowed_tables
            else "（当前没有可访问的表）"
        )
        raise SqlValidationError(
            f"表 '{unauthorized[0]}' 不在允许访问的表范围内，仅允许访问：{allowed_txt}"
        )


# ---------------------------------------------------------------------------
# 4) 表名单值规范化（供 get_table_data 这类"单表名参数"工具使用）
# ---------------------------------------------------------------------------
def normalize_plain_table_name(name: Any) -> str:
    """
    把模型传入的单表名规范化为安全标识符（反引号包裹的写法会被解包）。

    通过后才允许参与 SQL 拼接（白名单比对后拼接的是白名单内的确切表名）。
    校验失败抛 SqlValidationError。注意：这只保证"表名是普通标识符"，
    真正防越权的是紧随其后的 validate_allowed_tables。
    """
    if not isinstance(name, str) or not name.strip():
        raise SqlValidationError("表名不能为空。")
    raw = name.strip()
    if raw.startswith("`"):
        if not (raw.endswith("`") and len(raw) >= 2 and "`" not in raw[1:-1]):
            raise SqlValidationError(
                "反引号表名格式不正确，请直接使用 list_sql_tables 返回的表名。"
            )
        raw = raw[1:-1]
    if not _SAFE_IDENTIFIER_RE.match(raw):
        raise SqlValidationError(
            "表名只能由字母、数字、下划线组成且不能以数字开头；"
            "如需访问其他表请先通过 list_sql_tables 确认表名。"
        )
    return raw


# ---------------------------------------------------------------------------
# 5) 参数容器校验（动态值必须经数据库驱动绑定，禁止字符串拼接）
# ---------------------------------------------------------------------------
def validate_params(params: Any) -> Optional[Any]:
    """校验 params 容器类型（tuple/list/dict，与 mysql-connector 绑定一致）。"""
    if params is None:
        return None
    if isinstance(params, (tuple, list, dict)):
        return params
    raise SqlValidationError(
        "params 参数仅支持 tuple/list/dict（数据库驱动参数绑定），不支持其他格式。"
    )


# ---------------------------------------------------------------------------
# 组合入口：校验通过后返回 (语句类型, 原样 SQL, 原样 params) 供 cursor.execute
# ---------------------------------------------------------------------------
def prepare_read_only_query(
    sql: Any,
    params: Any = None,
    allowed_tables: Optional[set] = None,
):
    """
    执行完整只读安全校验，通过则返回 (keyword, sql, params) 三元组。

    - sql / params 原样返回，绝不在本层做任何字符串拼接（参数化由驱动完成）；
    - allowed_tables 为 None 时跳过白名单比对（仅测试/内部使用；
      db_tools 实际调用总会传入真实白名单）；
    - 任一步校验失败抛 SqlValidationError。
    """
    keyword = validate_read_only_sql(sql)
    bound_params = validate_params(params)
    if allowed_tables is not None:
        tables = extract_table_names(sql)
        validate_allowed_tables(tables, allowed_tables)
    return keyword, sql, bound_params
