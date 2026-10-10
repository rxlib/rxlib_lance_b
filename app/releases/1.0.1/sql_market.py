# coding: utf-8
"""
sql_market —— 直连 MySQL 行情库的取数 SQL。

仅在直连数据库模式（MysqlProvider / rx_core）下使用；API 模式不加载本模块。

规则：
  - 表名 / 列名只能取自本模块内的白名单，动态拼接前必须校验；
  - 业务值一律走参数绑定（pymysql 的 %s 占位），不做字符串插值。
"""
import re

# 库内主键在 SQL 内即换算为对外身份：
#   rxcode：交易所前置（SZ.000001），全系统唯一；
#   code ：6 位码（000001），可能重复，仅用于展示。
_RXCODE_EXPR = ("CONCAT(SUBSTRING_INDEX(full_code, '.', -1), '.', "
                "SUBSTRING_INDEX(full_code, '.', 1)) AS rxcode")
_CODE_EXPR = "SUBSTRING_INDEX(full_code, '.', 1) AS code"
_IDENTITY = _RXCODE_EXPR + ', ' + _CODE_EXPR


def _identity_for(alias):
    col = alias + '.full_code'
    return ("CONCAT(SUBSTRING_INDEX(%s, '.', -1), '.', "
            "SUBSTRING_INDEX(%s, '.', 1)) AS rxcode, "
            "SUBSTRING_INDEX(%s, '.', 1) AS code" % (col, col, col))


# 行情读取列（SQL 直接产出 rxcode/code + 库内原始长名）
KLINE_COLS = (_IDENTITY + ', trade_date, open_price, high_price, low_price, '
              'close_price, volume, limit_up_price, limit_down_price, is_st')

_MONTH_TABLE_RE = re.compile(r'^stock_daily_\d{6}$')

# 允许出现在查询中的列白名单
_KLINE_COLUMNS = frozenset(c.strip() for c in KLINE_COLS.split(','))
_BASE_COLUMNS = frozenset({'full_code', 'name', 'list_date', 'is_st', 'type',
                           'is_active'})
_ALLOWED_COLUMNS = _KLINE_COLUMNS | _BASE_COLUMNS


def safe_month_table(name):
    """校验月分片表名，返回加反引号的安全标识符，非法时抛 ValueError。"""
    if not _MONTH_TABLE_RE.match(name):
        raise ValueError('非法月分片表名: %r' % name)
    return '`%s`' % name


def safe_columns(cols):
    """逗号分隔列串按白名单逐项校验，原样返回（调用方内部常量，受控）。"""
    items = [c.strip() for c in cols.split(',') if c.strip()]
    for c in items:
        if c not in _ALLOWED_COLUMNS:
            raise ValueError('非法列名: %r' % c)
    return ', '.join(items)


def universe_all_market(prefix_count):
    """全市场股票池：prefix_count 个库内主键 LIKE %s 前缀子句。"""
    clauses = ' OR '.join(['full_code LIKE %s'] * int(prefix_count))
    return ('SELECT %s, name, list_date, is_st FROM stock_base '
            'WHERE type = 1 AND is_active = 1 AND (%s) '
            'ORDER BY rxcode' % (_IDENTITY, clauses))


def kline_by_codes(table, code_count, cols=KLINE_COLS):
    """单月表按库内主键 IN (...) 取行情，占位符按代码个数生成。"""
    ph = ','.join(['%s'] * int(code_count))
    return ('SELECT %s FROM %s WHERE full_code IN (%s)'
            % (cols, safe_month_table(table), ph))


def kline_all(table, cols=KLINE_COLS):
    """单月表全量行情。"""
    return 'SELECT %s FROM %s' % (cols, safe_month_table(table))


def kline_empty(table, cols=KLINE_COLS):
    """单月表空结果（仅取列结构）。"""
    return 'SELECT %s FROM %s WHERE 1 = 0' % (cols, safe_month_table(table))


def tables_in_schema(table_count):
    """按给定表名清单查当前库内实际存在的表（INFORMATION_SCHEMA）。"""
    ph = ','.join(['%s'] * int(table_count))
    return ('SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES '
            'WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s)' % ph)


def max_trade_date(table):
    """单月表最大交易日。"""
    return 'SELECT MAX(trade_date) AS m FROM %s' % safe_month_table(table)


def benchmark_close(table):
    """单月表内按库内主键取基准指数收盘价（参数：库内主键）。"""
    return ('SELECT trade_date, close_price FROM %s '
            'WHERE full_code = %%s' % safe_month_table(table))


# ---------------------------------------------------------------------------
# 盘中快照（stock_instant_YYYYMM）
# ---------------------------------------------------------------------------

INSTANT_COLS = (_IDENTITY + ', name, trade_date, pre_close, open_price, '
                'high_price, low_price, close_price, volume, amount, '
                'turnover_rate, is_st, tick_time')

_INSTANT_TABLE_RE = re.compile(r'^stock_instant_\d{6}$')

# 显式列名集合：INSTANT_COLS 含 CONCAT/SUBSTRING_INDEX 表达式，按逗号切会切碎
_INSTANT_COLUMNS = frozenset({
    'rxcode', 'code', 'name', 'trade_date', 'pre_close', 'open_price',
    'high_price', 'low_price', 'close_price', 'volume', 'amount',
    'turnover_rate', 'is_st', 'tick_time'})

_ALLOWED_COLUMNS = _ALLOWED_COLUMNS | _INSTANT_COLUMNS


def safe_instant_table(name):
    """校验盘中快照表名，返回加反引号的安全标识符，非法时抛 ValueError。"""
    if not _INSTANT_TABLE_RE.match(name):
        raise ValueError('非法盘中快照表名: %r' % name)
    return '`%s`' % name


def instant_tables():
    """当前库全部盘中快照表名（字面量 LIKE，不含外部输入）。"""
    return "SHOW TABLES LIKE 'stock_instant%'"


def instant_latest_date(table):
    """单表盘中快照的最大交易日与最新 tick。"""
    return ('SELECT MAX(trade_date) AS m, MAX(tick_time) AS t FROM %s'
            % safe_instant_table(table))


def instant_snapshot_all(table):
    """某日全市场盘中快照（参数：trade_date）。"""
    return ('SELECT %s FROM %s WHERE trade_date = %%s ORDER BY amount DESC'
            % (INSTANT_COLS, safe_instant_table(table)))
