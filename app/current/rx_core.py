# coding: utf-8
"""
rx_core —— quant 自包含的数据核心（不引用 quant 目录以外的任何文件）。

内容：
  - 只读 MySQL 连接与 universe / 月表 / K 线读取；
  - 内核用到的常量与 resolve_nproc。

技术指标已全部迁移至 breitstein.py（Breitstein 五要点体系），本模块不再提供指标计算。
本模块是 factor_lab / data_provider 的唯一外部依赖替代，复制后独立维护。
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import pymysql

import platform_env

try:
    import sql_market as SQL
except ImportError:
    class _MissingSQL(object):
        def __getattr__(self, name):
            raise RuntimeError(
                '缺少本地直连行情 SQL 模块 sql_market.py，无法使用直连 MySQL 模式；'
                '请改用 API 模式，或参照 sql_market.example.py 放置本地 sql_market.py')

    SQL = _MissingSQL()

READ_CONFIG = platform_env.get_db_config()

PREFIXES = ('000', '001', '002', '003', '300', '301', '302',
            '600', '601', '603', '605', '688', '689')

TOPN_NEW_PER_DAY = 20
MIN_ROWS = 30
NEW_LISTING_DAYS = 90


def to_code(c):
    s = str(c).strip()
    if '.' in s:
        # rxcode（SZ.000001）取点后；库内后缀式（000001.SZ）取点前
        left, right = s.split('.', 1)
        return right if left in ('SH', 'SZ', 'BJ') else left
    if s[:2] in ('SH', 'SZ', 'BJ') and s[2:].isdigit():
        return s[2:]
    return s


def to_full(code):
    s = str(code).strip()
    if '.' in s:
        left, right = s.split('.', 1)
        if left in ('SH', 'SZ', 'BJ'):
            # 已是 rxcode：转回库内后缀式
            return '%s.%s' % (right, left) if right.isdigit() else None
        # 已是库内后缀式
        return s if right in ('SH', 'SZ', 'BJ') and left.isdigit() else None
    if not s.isdigit():
        return None
    return s + ('.SH' if s.startswith(('6', '9')) else '.SZ')


def to_rxcode(symbol):
    """库内后缀式主键 → 交易所前置 rxcode：000001.SZ → SZ.000001。"""
    code, _, exch = str(symbol).partition('.')
    return '%s.%s' % (exch, code) if exch else code


def resolve_nproc():
    env = os.environ.get('B_T_NPROC')
    if env:
        return max(1, int(env))
    return max(1, (os.cpu_count() or 4) - 2)


def connect_replica_db(cfg=None):
    c = cfg or READ_CONFIG
    return pymysql.connect(host=c['host'], port=int(c.get('port', 3306)),
                           database=c['database'],
                           user=c['user'], password=c['password'],
                           charset='utf8mb4')


def load_universe(conn):
    """全市场股票池（自选股一律由上层经本地 CSV 的 custom_codes 提供）。"""
    params = tuple(p + '%' for p in PREFIXES)
    df = pd.read_sql(SQL.universe_all_market(len(PREFIXES)), conn,
                     params=params)
    df['list_date'] = pd.to_datetime(df['list_date'])
    df['rxcode'] = df['rxcode'].astype(str)
    df['code'] = df['code'].astype(str)
    is_st = df['is_st'].fillna(0).astype(int) == 1
    st_rx = set(df.loc[is_st, 'rxcode'])
    df = df[['rxcode', 'code', 'name', 'list_date', 'is_st']]
    return df, st_rx


def _read_one_table(args):
    tbl, cols, codes, read_config = args
    t0 = time.time()
    conn = connect_replica_db(read_config)
    try:
        codes = list(codes)
        if codes:
            frames = []
            bsz = 1000
            for b in range(0, len(codes), bsz):
                batch = codes[b:b + bsz]
                frames.append(pd.read_sql(
                    SQL.kline_by_codes(tbl, len(batch), cols),
                    conn, params=batch))
            d = pd.concat(frames, ignore_index=True) if frames else \
                pd.read_sql(SQL.kline_empty(tbl, cols), conn)
        else:
            d = pd.read_sql(SQL.kline_all(tbl, cols), conn)
    finally:
        conn.close()
    print('  read %s rows=%d %.2fs' % (tbl, len(d), time.time() - t0))
    return d


def discover_month_tables(conn, start, end_hint):
    cur0 = (start - pd.DateOffset(months=2)).to_period('M').to_timestamp()
    last = end_hint.to_period('M').to_timestamp()
    months = []
    while cur0 <= last:
        months.append('stock_daily_' + cur0.strftime('%Y%m'))
        cur0 = cur0 + pd.DateOffset(months=1)
    cur_sql = conn.cursor()
    try:
        cur_sql.execute(SQL.tables_in_schema(len(months)), tuple(months))
        found = sorted(r[0] for r in cur_sql.fetchall())
    finally:
        cur_sql.close()
    if not found:
        raise RuntimeError('未发现任何 stock_daily_YYYYMM 月表')
    return found


def load_kline(read_config, codes, tables):
    """codes 为 rxcode / 6 位 code 皆可：统一换算为库内主键做参数；rxcode 由 SQL 产出。"""
    symbols = {f for f in (to_full(c) for c in codes) if f}
    tasks = [(tbl, SQL.KLINE_COLS, symbols, read_config) for tbl in tables]
    with ThreadPoolExecutor(max_workers=len(tables)) as ex:
        frames = list(ex.map(_read_one_table, tasks))
    df = pd.concat(frames, ignore_index=True)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    for c in ('open_price', 'high_price', 'low_price', 'close_price',
              'limit_up_price', 'limit_down_price'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
    df['is_st'] = df['is_st'].fillna(0).astype(int)
    df['rxcode'] = df['rxcode'].astype(str)
    df['code'] = df['code'].astype(str)
    df['open'] = df['open_price']
    df['high'] = df['high_price']
    df['low'] = df['low_price']
    df['close'] = df['close_price']
    df = df.sort_values(['rxcode', 'trade_date']).reset_index(drop=True)
    return df


def discover_instant_tables(conn, day):
    """按交易日定位所在月的 stock_instant_YYYYMM 表（可能为空）。"""
    name = 'stock_instant_' + pd.Timestamp(day).strftime('%Y%m')
    cur = conn.cursor()
    try:
        cur.execute(SQL.tables_in_schema(1), (name,))
        return [r[0] for r in cur.fetchall()]
    finally:
        cur.close()


def latest_instant_date(read_config):
    """最新可用盘中快照交易日；无任何合法 instant 月表时返回 None。"""
    conn = connect_replica_db(read_config)
    try:
        cur = conn.cursor()
        cur.execute(SQL.instant_tables())
        tabs = []
        for (name,) in cur.fetchall():
            try:
                SQL.safe_instant_table(name)   # 白名单校验，排除 *_copy
            except ValueError:
                continue
            tabs.append(name)
        for tbl in sorted(tabs, reverse=True):
            cur.execute(SQL.instant_latest_date(tbl))
            row = cur.fetchone()
            if row and row[0] is not None:
                return pd.Timestamp(row[0]).date()
    finally:
        conn.close()
    return None


def load_instant(read_config, day, tables=None):
    """读某交易日的盘中快照，返回 (df, tick_time)。

    同股在同一日内可能有多个 tick，只保留 tick 最新的一条；
    tick_time 统一格式化为 HH:MM:SS（与服务端接口口径一致）。
    """
    day = pd.Timestamp(day)
    conn = connect_replica_db(read_config)
    try:
        tabs = tables if tables is not None else discover_instant_tables(conn, day)
        frames = []
        for tbl in tabs:
            d = pd.read_sql(SQL.instant_snapshot_all(tbl), conn,
                            params=(day.strftime('%Y-%m-%d'),))
            if len(d):
                frames.append(d)
    finally:
        conn.close()
    if not frames:
        return pd.DataFrame(), None
    df = pd.concat(frames, ignore_index=True)
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df['tick_time'] = pd.to_datetime(
        df['tick_time'], errors='coerce').dt.strftime('%H:%M:%S').fillna('')
    for c in ('pre_close', 'open_price', 'high_price', 'low_price',
              'close_price', 'turnover_rate'):
        df[c] = pd.to_numeric(df[c], errors='coerce')
    for c in ('volume', 'amount'):
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    df['is_st'] = df['is_st'].fillna(0).astype(int)
    df['rxcode'] = df['rxcode'].astype(str)
    df['code'] = df['code'].astype(str)
    df = df.sort_values(['rxcode', 'tick_time']).drop_duplicates(
        subset=['rxcode'], keep='last')
    tick = df['tick_time'].max()
    tick = str(tick) if tick else None
    df['open'] = df['open_price']
    df['high'] = df['high_price']
    df['low'] = df['low_price']
    df['close'] = df['close_price']
    df['pre_close_price'] = df['pre_close']
    return df.sort_values('rxcode').reset_index(drop=True), tick

