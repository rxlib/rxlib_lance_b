# coding: utf-8
"""
sql_queries —— 兼容聚合入口（已按用途拆分）。

SQL 语句现拆分为：
  - sql_market：直连 MySQL 行情库的取数语句，仅供本地使用，不纳入代码仓库
                （接口模板见 sql_market.example.py，使用时复制为 sql_market.py）；
  - sql_system：系统级与连通性自检语句，随仓库发布。

本文件保留为兼容入口；本地存在 sql_market 时转出其名称，缺失时降级为 None，
不影响 API 模式启动。新代码请直接导入对应模块。
"""
try:
    from sql_market import (
        KLINE_COLS,
        INSTANT_COLS,
        safe_month_table,
        safe_columns,
        universe_all_market,
        kline_by_codes,
        kline_all,
        kline_empty,
        tables_in_schema,
        max_trade_date,
        benchmark_close,
        safe_instant_table,
        instant_tables,
        instant_latest_date,
        instant_snapshot_all,
    )
except ImportError:
    KLINE_COLS = None
    INSTANT_COLS = None
    safe_month_table = None
    safe_columns = None
    universe_all_market = None
    kline_by_codes = None
    kline_all = None
    kline_empty = None
    tables_in_schema = None
    max_trade_date = None
    benchmark_close = None
    safe_instant_table = None
    instant_tables = None
    instant_latest_date = None
    instant_snapshot_all = None

from sql_system import (
    count_stock_base,
    show_tables,
)
