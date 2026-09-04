"""
execute_sql_query 真实 MySQL 集成测试（可选）

运行前提：
- mysql-connector-python / langchain-core / fastapi 等依赖已安装（可 import app.tools.db_tools）；
- docker compose 的本地 MySQL 教学库可用（docker/docker-compose.yaml + .env 配置）。

任一前提不满足时本文件整体 skip，不影响纯函数单元测试（test_db_tools.py）。
验证点：
1. 合法只读 SELECT 返回 CSV；
2. 写语句被工具层拒绝（不触达数据库）；
3. 越权表被拒绝；
4. 参数绑定：动态值经 %s 绑定而非字符串拼接（用含单引号的值证明不产生语法错误/注入）。
"""

import pytest

try:  # 依赖缺失 -> 整模块 skip
    from app.tools.db_tools import execute_sql_query, get_db_config
except Exception as exc:  # noqa: BLE001 - 环境缺失属预期，统一 skip
    pytest.skip(
        f"app.tools.db_tools 依赖不可用（{exc}），跳过真实 MySQL 集成测试",
        allow_module_level=True,
    )


def _db_reachable() -> bool:
    try:
        from mysql.connector import connect

        config = get_db_config()
        with connect(**config) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="本地 MySQL 教学库不可达（需 docker compose 启动），跳过集成测试",
)


def test_execute_select_returns_csv():
    result = execute_sql_query.invoke({"query": "SELECT * FROM drugs LIMIT 3"})
    assert "查询出现异常" not in result
    assert "SQL 校验未通过" not in result
    # 首行为列头，说明返回了真实 CSV 结构
    assert result.splitlines()[0]


def test_write_statement_rejected():
    result = execute_sql_query.invoke({"query": "DELETE FROM drugs"})
    assert "SQL 校验未通过" in result
    assert "只读" in result


def test_unauthorized_table_rejected():
    # 教学库只有 drugs/inventory/sales_records，越权表必然被拒
    result = execute_sql_query.invoke(
        {"query": "SELECT * FROM information_schema.tables"}
    )
    assert "不在允许访问的表范围内" in result


def test_parameter_binding_no_string_interpolation():
    # 值含单引号：若走字符串拼接会语法错误/注入，参数绑定则安全返回结果
    # generic_name 为 drugs 真实列（见 docker/mysql/mysql.sql 与 SHOW COLUMNS）；
    # 库中无 drug_name 列（历史笔误：此前 MySQL 不可达时本文件整体 skip，未暴露）
    sql = "SELECT * FROM drugs WHERE generic_name = %s"
    result = execute_sql_query.invoke({"query": sql, "params": ("测试药'名",)})
    assert "查询出现异常" not in result
