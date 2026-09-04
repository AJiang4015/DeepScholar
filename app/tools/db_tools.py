"""
MySQL 数据库查询工具模块

封装数据库查询助手使用的三个 LangChain 工具：
list_sql_tables 用于发现真实表名，get_table_data 用于预览字段和样例数据，
execute_sql_query 用于在确认结构后执行自定义查询。

安全边界（P001 修复，D007）：Agent 只能查询、不能修改数据库；可访问的表
必须在允许范围内；每次 execute_sql_query 都留下审计日志。校验逻辑为纯函数，
位于 app/utils/sql_security.py，便于单元测试。本模块只做数据库相关编排。
"""

import logging
import os
import time

from dotenv import load_dotenv
from langchain_core.tools import tool
from mysql.connector import Error, connect

from app.api.context import get_thread_context
from app.api.monitor import monitor
from app.utils.sql_security import (
    SqlValidationError,
    normalize_plain_table_name,
    prepare_read_only_query,
    validate_allowed_tables,
)

load_dotenv()

# ---------------------------------------------------------------------------
# 应用级 SQL 审计日志（最小实现：stdlib logging，不入库、不引基础设施）
# 审计只记录查询文本与状态，绝不记录 params 参数值（避免敏感数据进日志）
# ---------------------------------------------------------------------------
_audit_logger = logging.getLogger("deepsearch.audit.sql")
if not _audit_logger.handlers:
    _audit_handler = logging.StreamHandler()
    _audit_handler.setFormatter(
        logging.Formatter("%(asctime)s [SQL-AUDIT] %(levelname)s %(message)s")
    )
    _audit_logger.addHandler(_audit_handler)
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False


def _audit_sql(
    sql_type: str,
    sql: str,
    status: str,
    detail: str = "",
    duration_ms: float | None = None,
) -> None:
    """记录一次 SQL 校验/执行的审计信息。detail 只放错误摘要，不放参数值。"""
    try:
        thread_id = get_thread_context()
    except Exception:
        thread_id = None
    extra = f" detail={detail}" if detail else ""
    ms = f" duration_ms={duration_ms:.1f}" if duration_ms is not None else ""
    _audit_logger.info(
        "thread_id=%s sql_type=%s status=%s sql=%r%s%s",
        thread_id,
        sql_type,
        status,
        sql,
        ms,
        extra,
    )


# 集中读取数据库配置，后续三个工具都复用这份连接参数
def get_db_config():
    """
    从环境变量读取 MySQL 连接配置

    所有数据库工具都通过此函数拿到同一份连接参数，避免每个工具重复读取环境变量
    :return: mysql.connector.connect 可直接使用的连接参数
    """
    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
        "collation": os.getenv("MYSQL_COLLATION", "utf8mb4_unicode_ci"),
        "autocommit": True,
        "sql_mode": os.getenv("MYSQL_SQL_MODE", "TRADITIONAL"),
    }

    # 去掉未配置的可选项，避免把 None 传给 mysql.connector 造成连接参数异常
    config = {k: v for k, v in config.items() if v is not None}

    # user/password/database 是本教程工具能正常查询业务库的最小必要配置
    required_keys = ["user", "password", "database"]
    missing_keys = [k for k in required_keys if k not in config]
    if missing_keys:
        raise ValueError(f"缺失数据库核心配置：{', '.join(missing_keys)}")

    return config


def fetch_allowed_tables(config: dict) -> set:
    """
    查询当前数据库允许访问的表集合（与 list_sql_tables 的 SHOW TABLES 同源）。

    拿不到白名单就拒绝执行（fail-closed）：连接失败/查询失败时抛
    SqlValidationError，由调用方转成面向 Agent 的中文拒绝提示。
    """
    try:
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = DATABASE()"
                )
                return {row[0] for row in cursor.fetchall()}
    except Error as exc:
        # 不把连接细节抛给模型，只留通用原因；详细异常由审计日志承载
        raise SqlValidationError(
            "无法获取允许访问的表清单（数据库连接或查询失败），本次查询已被拒绝。"
        ) from exc


@tool
def list_sql_tables() -> str:
    """
    查询当前数据库中所有可用表

    作用：让模型先识别真实可用的表名，方便后续预览表结构和编写自定义 SQL。
    注意：只有本工具返回的表名才在允许访问范围内（白名单以此为数据源）。
    :return: 有表：可用的表有：表1,表2,表3...
             没有表：没有可用的表
             出现异常：查询出现异常：异常信息
    """

    # 埋点：工具一被调用，前端可以展示当前正在查询数据库表名
    monitor.report_tool(tool_name="数据库表名查询工具：list_sql_tables", args={})

    # 加载数据库连接信息
    config = get_db_config()

    # MySQL 查询的固定步骤：
    # 1. 创建连接
    # 2. 创建 cursor
    # 3. 执行 SQL
    # 4. 获取返回结果
    # 5. 释放连接和 cursor 资源
    # 这里捕获异常并返回中文提示，避免工具报错直接中断 Agent 执行链路
    try:
        # 使用 with 管理连接和游标，查询结束后自动释放数据库资源
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                sql = "SHOW TABLES"
                cursor.execute(sql)

                # SHOW TABLES 返回形如：[("drugs",), ("inventory",), ("sales_records",)]
                tables = cursor.fetchall()
                if not tables:
                    return "没有可用的表"

                # 取每个元组的第一个元素，拼成模型容易阅读的表名列表
                table_names = [table[0] for table in tables]
                return f"可用的表有：{', '.join(table_names)}"
    except Error as e:
        return f"查询出现异常：{str(e)}"


@tool
def get_table_data(table_name) -> str:
    """
    查询指定表的前 100 行数据

    当前工具调用之前，应先调用 list_sql_tables 完成表名校验。
    安全约束（工具层强制）：表名必须是 list_sql_tables 返回的允许表之一，
    否则拒绝执行，防止表名拼接注入与越权访问。
    此工具的作用：
    1. 完成单表样例数据查询
    2. 为多表查询提供表结构信息和数据格式参考
    :param table_name: 表名（必须来自 list_sql_tables 返回结果）
    :return: CSV 格式数据
             1. 第一行是列信息，列之间使用英文逗号分隔
             2. 第二行开始是表数据，值之间也使用英文逗号分隔
             3. 行和行之间使用 \n 分隔
             4. 至多查询 100 条表数据
             例如：
                id,name,age\n -> 列头
                1,张三,18\n
                1,张三,18\n
                1,张三,18\n -> 至多查询 100 条
    """
    # 埋点：工具二被调用，前端可以展示当前正在预览哪张表
    monitor.report_tool(
        tool_name="数据库表数据查询工具：get_table_data",
        args={"table_name": table_name},
    )

    # 获取数据库参数
    config = get_db_config()

    # 查询流程同样是：连接 -> cursor -> 执行 SQL -> 获取列信息和数据 -> 自动释放资源
    try:
        # 白名单校验（fail-closed）：表名先规范化为安全标识符，再与允许表比对；
        # 比对通过后拼接的 table 一定来自白名单集合，不存在注入面
        table = normalize_plain_table_name(table_name)
        allowed_tables = fetch_allowed_tables(config)
        validate_allowed_tables([table], allowed_tables)

        with connect(**config) as conn:
            with conn.cursor() as cursor:
                sql = f"SELECT * FROM {table} LIMIT 100"
                cursor.execute(sql)

                # cursor.description 保存查询结果的列元信息
                # 例如：[("id", ...), ("name", ...), ("age", ...)]
                # 如果 SQL 没有结果集，description 可能为 None
                description = cursor.description
                if not description:
                    return f"数据表 {table_name} 暂无数据。"

                # 只取每个列信息元组的第一个元素，也就是列名
                # 例如：["id", "name", "age"]
                columns = [desc[0] for desc in description]

                # fetchall 返回表数据，形如：[(1, "张三", 18), (2, "李四", 20)]
                rows = cursor.fetchall()

                # 把每一行数据从元组转成 CSV 行文本
                # 例如：(1, "张三", 18) -> "1,张三,18"
                results = [",".join(map(str, row)) for row in rows]

                # columns 组成 CSV 头部，rows 组成 CSV 数据体
                # 最终返回：
                # id,name,age
                # 1,张三,18
                # 1,张三,18
                header_str = ",".join(columns)
                data_str = "\n".join(results)
                return f"{header_str}\n{data_str}"
    except SqlValidationError as exc:
        # 校验失败（非法表名 / 未授权表 / 白名单获取失败）：拒绝并给模型可理解提示
        return f"查询被拒绝：{exc}"
    except Error as e:
        return f"查询出现异常：{str(e)}"


@tool
def execute_sql_query(query, params=None) -> str:
    """
    执行只读 SQL 查询（Agent 自定义查询入口）

    安全约束（工具层强制，不依赖提示词自觉）：
    1. 只允许单条只读语句：SELECT / SHOW / DESC / DESCRIBE / EXPLAIN；
       写语句（INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE/GRANT 等）、
       多语句、SELECT ... INTO OUTFILE/DUMPFILE 一律拒绝。
    2. 只允许访问 list_sql_tables 返回的表（表名白名单，FROM/JOIN/DESC 位置提取比对）。
    3. 动态值不要拼进 SQL 字符串：请通过 params 传入并配合 %s 占位符，
       由数据库驱动做参数绑定（tuple/list/dict 均可），例如：
       SELECT * FROM drugs WHERE price > %s
    注意：参数化只解决值注入；表名/列名无法用占位符参数化，因此表名白名单
    仍然强制生效。
    :param query: 只读 SQL 语句
    :param params: 可选参数绑定（tuple/list/dict），与 query 中的 %s 占位符一一对应
    :return: CSV 格式查询结果，或中文拒绝/错误提示
    """
    # 埋点：记录模型最终生成的 SQL，便于教学时观察是否真的落到了正确表字段上
    # 只上报参数是否存在，不上报参数值，避免敏感数据经 WS 外泄
    monitor.report_tool(
        tool_name="数据库表数据查询工具：execute_sql_query",
        args={"query": query, "params_provided": params is not None},
    )

    # 获取数据库参数
    config = get_db_config()

    started = time.perf_counter()
    sql_type = "unknown"
    try:
        # 1) 白名单数据源（fail-closed：拿不到就拒绝）
        allowed_tables = fetch_allowed_tables(config)
        # 2) 只读校验 -> 表名提取与白名单比对 -> 参数容器校验（纯函数，见 sql_security）
        sql_type, safe_query, safe_params = prepare_read_only_query(
            query, params, allowed_tables
        )
    except SqlValidationError as exc:
        _audit_sql(sql_type, str(query), "denied", detail=str(exc))
        return f"SQL 校验未通过：{exc}"

    try:
        # 3) 数据库驱动参数绑定执行（动态值经 %s 占位绑定，不做字符串拼接）
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                if safe_params is not None:
                    cursor.execute(safe_query, safe_params)
                else:
                    cursor.execute(safe_query)

                # 非查询类 SQL 没有结果集描述，这里统一返回提示，避免工具调用直接抛错给模型
                description = cursor.description
                if not description:
                    _audit_sql(
                        sql_type,
                        safe_query,
                        "no_result",
                        duration_ms=(time.perf_counter() - started) * 1000,
                    )
                    return f"执行自定义 SQL 语句没有查询结果，SQL 为：{query}"
                # description => [("列1", ...), ("列2", ...)]
                columns = [desc[0] for desc in description]

                # rows => [(值1, 值2), (值1, 值2)]
                rows = cursor.fetchall()

                # 每行元组统一转为逗号分隔文本，便于模型读取和后续整理
                results = [",".join(map(str, row)) for row in rows]

                # 第一行是列名，后续是查询数据
                header_str = ",".join(columns)
                data_str = "\n".join(results)

                _audit_sql(
                    sql_type,
                    safe_query,
                    "ok",
                    duration_ms=(time.perf_counter() - started) * 1000,
                )
                return f"{header_str}\n{data_str}"
    except Error as exc:
        # 详细异常写审计日志；给模型的提示保持简洁，不暴露连接细节
        _audit_sql(
            sql_type,
            safe_query,
            "error",
            detail=str(exc),
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        return f"查询出现异常：{str(exc)}"


if __name__ == "__main__":
    # 本地调试入口：直接运行本文件可验证 .env 中的 MySQL 连接配置是否可用
    print(
        execute_sql_query.invoke(
            {
                "query": "SELECT * FROM `drugs` dgs join sales_records srd on dgs.drug_id = srd.drug_id"
            }
        )
    )
