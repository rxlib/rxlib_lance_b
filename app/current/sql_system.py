# coding: utf-8
"""
sql_system —— 系统级与连通性自检 SQL。

用于数据库连接测试、库表枚举等管理用途，与行情取数语句分开维护。

规则：
  - 仅放结构固定、不含外部业务值的语句；
  - 不做表名 / 列名的字符串拼接。
"""


def count_stock_base():
    """连通性自检：stock_base 行数。"""
    return 'SELECT COUNT(*) FROM stock_base'


def show_tables():
    """当前库全部表名。"""
    return 'SHOW TABLES'
