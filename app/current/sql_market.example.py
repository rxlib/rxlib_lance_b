# coding: utf-8
import re

# 月分片表名白名单：stock_daily_YYYYMM
_MONTH_TABLE_RE = re.compile(r'^stock_daily_\d{6}$')

# 默认行情列（rxcode/code 身份列 + 行情原始列），按需在实体文件中补全。
KLINE_COLS = ''

# 盘中快照列（rxcode/code 身份列 + 快照原始列），按需在实体文件中补全。
INSTANT_COLS = ''

# 允许出现在查询中的列白名单，实体文件中补全。
_ALLOWED_COLUMNS = frozenset()


def safe_month_table(name):
    """校验月分片表名，返回加反引号的安全标识符，非法时抛 ValueError。"""
    if not _MONTH_TABLE_RE.match(name):
        raise ValueError('非法月分片表名: %r' % name)
    return '`%s`' % name


def safe_columns(cols):
    """逗号分隔列串按白名单逐项校验后返回。"""
    raise NotImplementedError('请在本地 sql_market.py 中实现')


def universe_all_market(prefix_count):
    """全市场股票池 SQL；含 prefix_count 个 LIKE %s 前缀占位。"""
    raise NotImplementedError('请在本地 sql_market.py 中实现')


def kline_by_codes(table, code_count, cols=KLINE_COLS):
    """单月表按 full_code IN (...) 取行情；占位符个数 = code_count。"""
    raise NotImplementedError('请在本地 sql_market.py 中实现')


def kline_all(table, cols=KLINE_COLS):
    """单月表全量行情 SQL。"""
    raise NotImplementedError('请在本地 sql_market.py 中实现')


def kline_empty(table, cols=KLINE_COLS):
    """单月表空结果 SQL（仅取列结构）。"""
    raise NotImplementedError('请在本地 sql_market.py 中实现')


def tables_in_schema(table_count):
    """INFORMATION_SCHEMA 月表发现 SQL；占位符个数 = table_count。"""
    raise NotImplementedError('请在本地 sql_market.py 中实现')


def max_trade_date(table):
    """单月表最大交易日 SQL。"""
    raise NotImplementedError('请在本地 sql_market.py 中实现')


def benchmark_close(table):
    """单月表内取基准指数收盘价 SQL；参数：full_code（%s）。"""
    raise NotImplementedError('请在本地 sql_market.py 中实现')


# ---- 盘中快照（stock_instant_YYYYMM）----

_INSTANT_TABLE_RE = re.compile(r'^stock_instant_\d{6}$')


def safe_instant_table(name):
    """校验盘中快照表名，返回加反引号的安全标识符，非法时抛 ValueError。"""
    if not _INSTANT_TABLE_RE.match(name):
        raise ValueError('非法盘中快照表名: %r' % name)
    return '`%s`' % name


def instant_tables():
    """当前库全部盘中快照表名 SQL（SHOW TABLES LIKE）。"""
    raise NotImplementedError('请在本地 sql_market.py 中实现')


def instant_latest_date(table):
    """单表盘中快照的最大交易日 / 最新 tick SQL。"""
    raise NotImplementedError('请在本地 sql_market.py 中实现')


def instant_snapshot_all(table):
    """某日全市场盘中快照 SQL；参数：trade_date（%s）。"""
    raise NotImplementedError('请在本地 sql_market.py 中实现')
