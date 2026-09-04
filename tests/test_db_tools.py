"""
db_tools 安全加固的单元测试（P001 / D007）

测试对象是 app/utils/sql_security.py 的纯函数校验层：
- validate_read_only_sql：语句类型 / 多语句 / 注释与大小写绕过 / 副作用导出
- extract_table_names：FROM / JOIN / 逗号表列表 / 子查询 / 派生表拒绝
- validate_allowed_tables：表名白名单（fail-closed）
- normalize_plain_table_name：单表名参数规范化
- prepare_read_only_query：组合校验 + 参数化（证明值不进 SQL 字符串）

本文件不 import app.tools.db_tools（其依赖 mysql-connector / langchain /
fastapi，单元环境可能未安装），只测纯函数安全边界；
真实 MySQL 集成测试见 test_db_tools_mysql_integration.py（无环境自动 skip）。
"""

import pytest

from app.utils.sql_security import (
    SqlValidationError,
    extract_table_names,
    normalize_plain_table_name,
    prepare_read_only_query,
    validate_allowed_tables,
    validate_read_only_sql,
)


# ---------------------------------------------------------------------------
# 1) 只读语句类型校验：正常情况
# ---------------------------------------------------------------------------
class TestReadOnlyAllowed:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM users;",
            "SHOW TABLES;",
            "DESC users;",
            "DESCRIBE users;",
            "EXPLAIN SELECT * FROM users;",
            "select * from users",
            "  SeLeCt * FROM users  ",
            "SELECT COUNT(*), MAX(price) FROM orders GROUP BY user_id",
            "SELECT a.id, b.name FROM users a JOIN orders b ON a.id = b.user_id",
        ],
    )
    def test_allows_read_only(self, sql):
        assert validate_read_only_sql(sql) in {
            "SELECT",
            "SHOW",
            "DESC",
            "DESCRIBE",
            "EXPLAIN",
        }

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1; -- 尾部注释",
            "SELECT 1 # 尾部注释",
            "/* 前导块注释 */ SELECT * FROM users",
            "-- 行注释\nSELECT * FROM users",
            "# 行注释\nSELECT * FROM users",
            "SELECT * FROM users;",  # 末尾单个分号容忍
            "SELECT 'a;b' FROM users",  # 字符串内分号不算多语句
            "SELECT 1 FROM users WHERE name = 'O''Brien'",  # 双写引号转义
        ],
    )
    def test_allows_with_comments_and_semicolon_in_string(self, sql):
        assert validate_read_only_sql(sql) in {
            "SELECT",
            "SHOW",
            "DESC",
            "DESCRIBE",
            "EXPLAIN",
        }


# ---------------------------------------------------------------------------
# 2) 只读语句类型校验：禁止情况
# ---------------------------------------------------------------------------
class TestReadOnlyDenied:
    @pytest.mark.parametrize(
        "sql",
        [
            "DELETE FROM users;",
            "UPDATE users SET name='x';",
            "DROP TABLE users;",
            "TRUNCATE TABLE users;",
            "INSERT INTO users VALUES (1);",
            "ALTER TABLE users ADD COLUMN x INT;",
            "CREATE TABLE tmp (id INT);",
            "GRANT ALL ON *.* TO 'x';",
            "REVOKE ALL ON *.* FROM 'x';",
            "REPLACE INTO users VALUES (1);",
            "CALL sp_drop();",
            "SET sql_mode = '';",
            "USE other_db;",
        ],
    )
    def test_rejects_write_statements(self, sql):
        with pytest.raises(SqlValidationError):
            validate_read_only_sql(sql)

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM users; DELETE FROM users;",  # 多语句
            "SELECT 1; DROP TABLE users",  # 多语句（写）
            "SELECT 1;;",  # 双分号
            "",  # 空
            "   ",  # 纯空白
            "-- 只有注释",  # 只有注释
            "/* only comment */",  # 只有块注释
            "SELECT * FROM users;--\nDELETE FROM users",  # 分号 + 行注释再起写语句
            "/*!50000 DELETE */ FROM users",  # MySQL 可执行版本化注释绕过
            "SELECT * INTO OUTFILE '/tmp/x' FROM users",  # 服务器端文件写入
            "SELECT * INTO DUMPFILE '/tmp/x' FROM users",
        ],
    )
    def test_rejects_multi_statement_and_bypass(self, sql):
        with pytest.raises(SqlValidationError):
            validate_read_only_sql(sql)

    def test_rejects_case_insensitive_write(self):
        with pytest.raises(SqlValidationError):
            validate_read_only_sql("dRoP TaBlE users")

    def test_rejects_with_cte_prefix(self):
        # WITH 前缀 CTE 不在支持范围：fail-closed
        with pytest.raises(SqlValidationError):
            validate_read_only_sql("WITH t AS (SELECT * FROM users) SELECT * FROM t")

    def test_rejects_non_string_input(self):
        with pytest.raises(SqlValidationError):
            validate_read_only_sql(None)
        with pytest.raises(SqlValidationError):
            validate_read_only_sql(12345)


# ---------------------------------------------------------------------------
# 3) 表名提取
# ---------------------------------------------------------------------------
class TestExtractTableNames:
    def test_single_table(self):
        assert extract_table_names("SELECT * FROM users;") == ["users"]

    def test_join_tables(self):
        sql = "SELECT a.id, b.name FROM users a JOIN orders b ON a.id = b.user_id"
        assert extract_table_names(sql) == ["users", "orders"]

    def test_multiple_joins(self):
        sql = (
            "SELECT * FROM drugs d "
            "JOIN inventory i ON d.drug_id = i.drug_id "
            "JOIN sales_records s ON d.drug_id = s.drug_id"
        )
        assert extract_table_names(sql) == ["drugs", "inventory", "sales_records"]

    def test_comma_separated_tables(self):
        assert extract_table_names("SELECT * FROM users, orders") == ["users", "orders"]

    def test_comma_list_with_as_alias_not_bypass(self):
        # 回归：FROM t1 AS x, t2 —— AS 别名后仍是表列表，t2 必须被提取
        sql = "SELECT * FROM users AS u, orders o"
        assert extract_table_names(sql) == ["users", "orders"]

    def test_where_subquery_table(self):
        sql = "SELECT * FROM users WHERE id IN (SELECT user_id FROM orders)"
        assert set(extract_table_names(sql)) == {"users", "orders"}

    def test_schema_qualified_table_takes_last_part(self):
        assert extract_table_names("SELECT * FROM myschema.users") == ["users"]

    def test_backtick_table(self):
        assert extract_table_names("SELECT * FROM `users`") == ["users"]
        assert extract_table_names("SELECT * FROM `myschema`.`users`") == ["users"]

    def test_desc_table(self):
        assert extract_table_names("DESC users") == ["users"]

    def test_explain_select(self):
        assert extract_table_names("EXPLAIN SELECT * FROM users") == ["users"]

    def test_select_without_tables(self):
        assert extract_table_names("SELECT 1") == []

    def test_derived_table_rejected(self):
        with pytest.raises(SqlValidationError):
            extract_table_names("SELECT * FROM (SELECT * FROM users) t")

    def test_table_function_rejected(self):
        # FROM 后跟 '('（表函数/派生表）不在支持范围，fail-closed
        with pytest.raises(SqlValidationError):
            extract_table_names("SELECT * FROM JSON_TABLE(...) t")


# ---------------------------------------------------------------------------
# 4) 表名白名单
# ---------------------------------------------------------------------------
class TestAllowedTables:
    ALLOWED = {"users", "orders"}

    def test_allowed_tables_pass(self):
        validate_allowed_tables(["users"], self.ALLOWED)
        validate_allowed_tables(["orders"], self.ALLOWED)
        validate_allowed_tables(["users", "orders"], self.ALLOWED)

    def test_unauthorized_table_rejected(self):
        with pytest.raises(SqlValidationError) as exc_info:
            validate_allowed_tables(["payments"], self.ALLOWED)
        assert "payments" in str(exc_info.value)
        assert "users" in str(exc_info.value)

    def test_partial_unauthorized_rejected(self):
        # JOIN 中一个表越权 -> 整体拒绝（fail-closed）
        with pytest.raises(SqlValidationError):
            validate_allowed_tables(["users", "payments"], self.ALLOWED)

    def test_empty_tables_ok(self):
        validate_allowed_tables([], self.ALLOWED)

    def test_case_sensitive_fail_closed(self):
        # MySQL 表名在 Linux 上区分大小写；大小写不一致按未授权拒绝
        with pytest.raises(SqlValidationError):
            validate_allowed_tables(["USERS"], self.ALLOWED)

    def test_join_whitelist_combination(self):
        validate_allowed_tables(["users", "orders"], self.ALLOWED)


# ---------------------------------------------------------------------------
# 5) 单表名规范化（get_table_data 入口）
# ---------------------------------------------------------------------------
class TestNormalizePlainTableName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("users", "users"),
            ("`users`", "users"),
            ("drugs", "drugs"),
        ],
    )
    def test_normalizes(self, raw, expected):
        assert normalize_plain_table_name(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "",  # 空
            "  ",  # 纯空白
            "users; DROP TABLE users",  # 注入尝试
            "users/../secret",  # 路径穿越尝试
            "dr ug s",  # 空格
            "1abc",  # 数字开头
            "`users`x",  # 畸形反引号
            "`a`b`",  # 反引号未闭合/多反引号
            None,
            123,
        ],
    )
    def test_rejects_unsafe(self, raw):
        with pytest.raises(SqlValidationError):
            normalize_plain_table_name(raw)


# ---------------------------------------------------------------------------
# 6) 组合校验 + 参数化：动态值不得进入 SQL 字符串
# ---------------------------------------------------------------------------
class TestPrepareReadOnlyQuery:
    ALLOWED = {"users", "orders"}

    def test_params_never_interpolated_into_sql(self):
        sql = "SELECT * FROM users WHERE age > %s AND name = %s"
        params = (18, "O'Brien")
        keyword, out_sql, out_params = prepare_read_only_query(
            sql, params, self.ALLOWED
        )
        assert keyword == "SELECT"
        # SQL 原样返回：值没有拼进字符串（参数化由驱动 %s 绑定完成）
        assert out_sql == sql
        assert out_params == (18, "O'Brien")

    def test_dict_params_passthrough(self):
        sql = "SELECT * FROM users WHERE age > %(age)s"
        params = {"age": 18}
        _, out_sql, out_params = prepare_read_only_query(sql, params, self.ALLOWED)
        assert out_sql == sql
        assert out_params == {"age": 18}

    def test_params_none_ok(self):
        keyword, out_sql, out_params = prepare_read_only_query(
            "SELECT * FROM users", None, self.ALLOWED
        )
        assert keyword == "SELECT"
        assert out_params is None

    def test_invalid_params_type_rejected(self):
        with pytest.raises(SqlValidationError):
            prepare_read_only_query(
                "SELECT * FROM users WHERE age > %s", 18, self.ALLOWED
            )

    def test_write_sql_rejected_even_with_params(self):
        with pytest.raises(SqlValidationError):
            prepare_read_only_query("DELETE FROM users", (), self.ALLOWED)

    def test_unauthorized_table_rejected_with_params(self):
        with pytest.raises(SqlValidationError):
            prepare_read_only_query(
                "SELECT * FROM payments WHERE x = %s", (1,), self.ALLOWED
            )

    def test_join_both_allowed(self):
        sql = (
            "SELECT * FROM users u JOIN orders o ON u.id = o.user_id WHERE o.total > %s"
        )
        keyword, _, _ = prepare_read_only_query(sql, (100,), self.ALLOWED)
        assert keyword == "SELECT"

    def test_join_one_unauthorized_rejected(self):
        sql = "SELECT * FROM users u JOIN payments p ON u.id = p.user_id"
        with pytest.raises(SqlValidationError) as exc_info:
            prepare_read_only_query(sql, None, self.ALLOWED)
        assert "payments" in str(exc_info.value)

    def test_comma_list_alias_unauthorized_rejected(self):
        # 回归：FROM users AS u, payments p —— AS 别名后逗号表的越权表必须被拒
        sql = "SELECT * FROM users AS u, payments p"
        with pytest.raises(SqlValidationError) as exc_info:
            prepare_read_only_query(sql, None, self.ALLOWED)
        assert "payments" in str(exc_info.value)

    def test_allowed_tables_none_skips_whitelist(self):
        # 内部/测试用途：不传白名单时跳过比对（db_tools 实际总会传入真实白名单）
        keyword, _, _ = prepare_read_only_query("SELECT * FROM anything", None, None)
        assert keyword == "SELECT"
