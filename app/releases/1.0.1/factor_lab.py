# coding: utf-8
"""
factor_lab —— Lance B 支撑与阻力五要点（活跃度/干净价位/盘整/情绪/多周期共振）统一组合回测框架（长期维护的单一研究入口）。

目标：
  结束"每问一个问题就新建一个临时脚本"的状态。把 Lance B 支撑与阻力五要点（①活跃度→②干净价位→③盘整→④情绪反应→⑤多周期共振）一次性纳入面板，用声明式筛选器组合入场口径，经唯一撮合引擎重放，统一以日复利账户净值口径评估，结果落本地 CSV。

边界：
  - 只读本地从库 rxdata 月表，不写任何数据库表；
  - 不 import / 不依赖任何外部交易客户端函数，不修改生产三件套；
  - 经本目录内自包含模块 rx_core 复用只读数据函数，指标由 breitstein.py 提供；
  - 默认零佣金零滑点；--fee-bps 可设单边费率（单位 bp，万 3 = 3），买卖各扣一次。

⚠️ 仅交易学习，不构成投资建议。

撮合口径：
  买入：信号日收盘价成交（signal_date 即成交日）；当日收盘价为 ST 或收在涨停则不成交；
  卖出：出场信号按当日收盘价成交；结构/固定止损（当日 low 判价、同 bar 先止损后止盈）、
        max_hold 到期收盘卖；
  分批：同一标的可多批次进场（LPS 试仓 → SOS/BU 加仓），加权平均成本；
  未平仓：区间结束时仍持有的仓位按区间最后交易日收盘价估值列出（exit_reason='open'），
        其浮动盈亏计入净值，但不计入胜率/盈亏比/交易数；
  净值：每日等权 port_ret 复利；在仓股按 昨收->今收 计入当日收益，与交易明细逐笔自洽。

运行：
  .\\runtime\\python312\\python.exe -B -u factor_lab.py
  可选 --stage {grid,presets,both,picks} --date YYYY-MM-DD --pick-mode {spring,lps,sos,ut,sow}
        --entry-path {lps_sos,sos_bu} --topn 5 --fee-bps 0 --plot --rebuild-cache
输出（OUT_DIR，统一 factor_lab_ 前缀）：
  factor_lab_trades_<tag>_<cid>.csv / factor_lab_equity_<tag>_<cid>.csv
  factor_lab_grid_<tag>.csv / factor_lab_presets_<tag>.csv
"""
import os
import sys
import time
import pickle
import argparse
import itertools

import numpy as np
import pandas as pd

import rx_core as PB
import breitstein as BT

import platform_env
import cache_crypto

try:
    import sql_market as SQL
except ImportError:
    class _MissingSQL(object):
        def __getattr__(self, name):
            raise RuntimeError(
                '缺少本地直连行情 SQL 模块 sql_market.py，无法使用直连 MySQL 模式；'
                '请改用 API 模式，或参照 sql_market.example.py 放置本地 sql_market.py')

    SQL = _MissingSQL()

OUT_DIR = platform_env.RESULTS_DIR
platform_env.ensure_dirs()
MIN_ROWS = PB.MIN_ROWS
NEW_LISTING_DAYS = PB.NEW_LISTING_DAYS

PANEL_VERSION = 10

DEFAULT_BENCHMARK = '000300'

BOARD_PREFIXES = {
    '沪市主板': ('600', '601', '603', '605'),
    '深市主板': ('000', '001', '002', '003'),
    '创业板': ('300', '301', '302'),
    '科创板': ('688', '689'),
}

TEST_POOL_CODES = (
    '688012', '688047', '688041', '688120', '688017',
    '300666', '688777', '688271', '002222', '603688',
)


def test_pool_rxcodes():
    import rx_core as _pb
    return [_pb.to_rxcode(_pb.to_full(c)) for c in TEST_POOL_CODES]


BOARD_ORDER = ('科创板', '创业板', '深市主板', '沪市主板')

# 面板字段映射 (calc_breitstein 列名 → 面板键)；Breitstein 列名为大写
_FLOAT_KEYS = [
    ('open', 'open'), ('high', 'high'), ('low', 'low'),
    ('close', 'close'), ('volume', 'volume'), ('limit_up', 'limit_up'),
    ('ATR_RATIO', 'atr_ratio'), ('VOLUME_RATIO', 'volume_ratio'),
    ('GAP_PCT', 'gap_pct'), ('ACTIVITY_RAW', 'activity_raw'),
    ('PIVOT_H', 'pivot_h'), ('PIVOT_L', 'pivot_l'),
    ('LEVEL_SUPPORT', 'level_support'), ('LEVEL_RESISTANCE', 'level_resistance'),
    ('LEVEL_CONF_S', 'level_conf_s'), ('LEVEL_CONF_R', 'level_conf_r'),
    ('TOUCHES_S', 'touches_s'), ('TOUCHES_R', 'touches_r'),
    ('BREAKOUT_ROC', 'breakout_roc'), ('AVWAP', 'avwap'), ('AVWAP_SIDE', 'avwap_side'),
    ('RANGE_N', 'range_n'), ('RANGE_PCT', 'range_pct'), ('RANGE_Z', 'range_z'),
    ('NET_DRIFT', 'net_drift'), ('REG_SLOPE', 'reg_slope'), ('CV', 'cv'),
    ('VOLUME_SLOPE', 'volume_slope'),
    ('BB_Z', 'bb_z'), ('VOLUME_Z', 'volume_z'), ('SENTIMENT_SCORE', 'sentiment_score'),
    ('VELOCITY', 'velocity'), ('ACCELERATION', 'acceleration'), ('ACCEL_Z', 'accel_z'),
    ('CONS_DOWN', 'cons_down'), ('EMOTION_SCORE', 'emotion_score'),
    ('DAILY_DIR', 'daily_dir'), ('WEEKLY_DIR', 'weekly_dir'), ('MONTHLY_DIR', 'monthly_dir'),
    ('RESONANCE', 'resonance'), ('RESONANCE_COUNT', 'resonance_count'),
    ('POSITION_MULT', 'position_mult'),
]
# Breitstein 五要点无字符串/对象型列，需映射为空列表。
_OBJ_KEYS = []
_BOOL_KEYS = [
    ('ACTIVE_ATR', 'active_atr'), ('ACTIVE_VOL', 'active_vol'),
    ('ACTIVE_GAP', 'active_gap'), ('IS_ACTIVE', 'is_active'),
    ('CLEAN_LEVEL', 'clean_level'),
    ('COMPRESSED', 'compressed'), ('UNIFORM', 'uniform'),
    ('VOLUME_TREND_OK', 'volume_trend_ok'), ('CONSOLIDATED', 'consolidated'),
    ('EMOTION_EXTREME', 'emotion_extreme'), ('SENTIMENT_EXTREME', 'sentiment_extreme'),
    ('SPEEDING_DOWN', 'speeding_down'), ('EMOTION_EXTREME_ZONE', 'emotion_extreme_zone'),
    ('CAPITULATION_BAR', 'capitulation_bar'), ('RECLAIM_AVWAP', 'reclaim_avwap'),
    ('ENTRY_REVERSAL', 'entry_reversal'), ('LEVEL_OVERLAP', 'level_overlap'),
]


# ==================== 层1：数据与 Breitstein 五要点面板 ====================

def _panel_work(args):
    rxcode, recs, list_date, name, code, bench_recs = args
    g = pd.DataFrame(recs).sort_values('trade_date')
    if len(g) < MIN_ROWS:
        return None
    # 基准按交易日对齐到个股（新股/停牌也能等长）；无基准时仅缺 RS
    bench_g = None
    if bench_recs:
        b = pd.DataFrame(bench_recs).sort_values('trade_date')
        bg = g[['trade_date']].merge(b, on='trade_date', how='left')
        if len(bg) == len(g) and bg['close'].notna().all():
            bench_g = bg[['open', 'high', 'low', 'close', 'volume']]
    d = BT.calc_breitstein(g.copy(), benchmark=bench_g)
    p = {'rxcode': rxcode, 'code': code, 'name': name,
         'list_date': list_date}
    p['dt'] = d['trade_date'].values
    for src, dst in _FLOAT_KEYS:
        p[dst] = pd.to_numeric(d[src], errors='coerce').astype(float).values
    for src, dst in _OBJ_KEYS:
        p[dst] = d[src].fillna('').astype(str).values
    p['is_st'] = pd.to_numeric(d['is_st'], errors='coerce').fillna(0).astype(int).values
    for src, dst in _BOOL_KEYS:
        p[dst] = d[src].fillna(False).values.astype(bool)
    return p


def build_panels(kline, universe, nproc):
    import multiprocessing as mp
    # rxcode 是唯一主键；list_date/name 均按 rxcode 映射（code 可能重复）
    list_map = universe.set_index('rxcode')['list_date'].to_dict()
    name_map = universe.set_index('rxcode')['name'].to_dict()
    code_map = universe.set_index('rxcode')['code'].to_dict()
    # 基准（沪深300）记录，供 worker 对齐
    bench_rx = PB.to_rxcode(PB.to_full(DEFAULT_BENCHMARK))
    bench_recs = None
    if bench_rx in set(kline['rxcode']):
        bench_recs = kline[kline['rxcode'] == bench_rx].to_dict('records')
    tasks = ((rx, g.to_dict('records'), list_map.get(rx),
              name_map.get(rx) or rx,
              code_map.get(rx, str(rx).split('.', 1)[-1]), bench_recs)
             for rx, g in kline.groupby('rxcode', sort=True))
    panels = {}
    t0 = time.time()
    if nproc <= 1:
        for a in tasks:
            r = _panel_work(a)
            if r is not None:
                panels[r['rxcode']] = r
    else:
        n_in = int(kline['rxcode'].nunique())
        with mp.Pool(processes=nproc) as pool:
            for done, r in enumerate(pool.imap_unordered(_panel_work, tasks, chunksize=8), 1):
                if r is not None:
                    panels[r['rxcode']] = r
                if done % 500 == 0 or done == n_in:
                    print('正在计算指标 %d/%d ...' % (done, n_in))
    for p in panels.values():
        p['pos'] = {pd.Timestamp(v): i for i, v in enumerate(p['dt'])}
    print('Breitstein 指标计算完成：%d 只，用时 %.1f 秒' % (len(panels), time.time() - t0))
    return panels


def normalize_code(c):
    s = PB.to_code(c).upper()
    # 兼容同花顺等导出：去掉 SH/SZ/BJ 市场前缀（如 SZ300014、SH600066）
    for pfx in ('SH', 'SZ', 'BJ'):
        if s.startswith(pfx):
            s = s[len(pfx):]
            break
    # Excel 打开会丢掉前导零（如 003931 变成 3931），统一补足 6 位
    if s.isdigit():
        s = s.zfill(6)
    return s if len(s) == 6 and s.isdigit() else None


def _to_full(code):
    return PB.to_full(code)


def normalize_rxcode(c):
    """任意入参（含 SH/SZ 前缀、后缀式、6 位码）→ 唯一 rxcode，无法识别返回 None。"""
    full = PB.to_full(c)
    return PB.to_rxcode(full) if full else None


def _normalize_identity(df):
    out = df.copy()
    if 'rxcode' not in out.columns:
        if 'code' in out.columns:
            out['rxcode'] = [
                PB.to_rxcode(full) if (full := PB.to_full(c)) else ''
                for c in out['code']]
        else:
            out['rxcode'] = ''
    rx_text = out['rxcode'].where(out['rxcode'].notna(), '').map(
        lambda x: str(x).strip())
    valid_rx = rx_text.map(lambda x: '.' in x)
    if 'code' in out.columns:
        code_text = out['code'].where(out['code'].notna(), '').map(PB.to_code)
    else:
        code_text = pd.Series('', index=out.index)
    rx_fixed = pd.Series(
        [PB.to_rxcode(full) if (full := PB.to_full(c)) else ''
         for c in code_text], index=out.index)
    out['rxcode'] = rx_text.where(valid_rx, rx_fixed).astype(str)
    out['code'] = out['rxcode'].map(
        lambda x: PB.to_code(x) if x else '').astype(str)
    return out


def filter_universe(universe, boards=None, exclude_st=False,
                    min_list_days=None, asof=None, extra_codes=None):
    out = _normalize_identity(universe)
    if boards:
        prefixes = tuple(p for b in boards for p in BOARD_PREFIXES.get(b, ()))
        if prefixes:
            out = out[out['code'].str.startswith(prefixes)]
    if exclude_st and 'is_st' in out.columns:
        out = out[out['is_st'].fillna(0).astype(int) == 0]
    if min_list_days and asof is not None:
        asof = pd.Timestamp(asof)
        days = (asof - pd.to_datetime(out['list_date'])).dt.days
        out = out[days >= int(min_list_days)]
    if extra_codes:
        have_rx = set(out['rxcode'])
        add = []
        for c in (normalize_code(x) for x in extra_codes):
            full = PB.to_full(c)
            if c and full and PB.to_rxcode(full) not in have_rx:
                add.append((c, full))
        if add:
            miss = pd.DataFrame(
                [dict(rxcode=PB.to_rxcode(full), code=c, name=c,
                      list_date=pd.NaT, is_st=0) for c, full in add])
            out = pd.concat([out, miss], ignore_index=True)
    return out.reset_index(drop=True)


from collections import OrderedDict

SHARD_VERSION = 2
_PANEL_MEMO = OrderedDict()
_PANEL_MEMO_MAX = 8

_INTRADAY_MEMO = OrderedDict()
_INTRADAY_MEMO_MAX = 4

# 盘中快照 TTL：窗口内复用结果，不向 API 发任何请求（含 304 协商）。
# key 为不含 tick_time 的请求口径，value 为 (fetched_monotonic, result)。
_INTRADAY_TTL = OrderedDict()
_INTRADAY_TTL_MAX = 8
DEFAULT_INTRADAY_TTL_SECONDS = 60


def get_intraday_ttl_seconds():
    """读取盘中快照 TTL（秒）：[intraday] snapshot_ttl_seconds，默认 60；0=关闭。"""
    import configparser
    platform_env.ensure_dirs()
    ini = os.path.join(platform_env.CONFIG_DIR, 'config.ini')
    value = DEFAULT_INTRADAY_TTL_SECONDS
    if os.path.exists(ini):
        cp = configparser.ConfigParser()
        cp.read(ini, encoding='utf-8-sig')
        if cp.has_section('intraday'):
            try:
                value = int(cp['intraday'].get(
                    'snapshot_ttl_seconds', DEFAULT_INTRADAY_TTL_SECONDS))
            except (TypeError, ValueError):
                value = DEFAULT_INTRADAY_TTL_SECONDS
    return max(0, value)


def _store_intraday_ttl(ttl_key, result, ttl_seconds, force=False):
    """把本次结果登记进 TTL；force=True 时刷新计时（强制拉取也续期）。"""
    if ttl_seconds <= 0:
        return
    if force and ttl_key in _INTRADAY_TTL:
        _INTRADAY_TTL.move_to_end(ttl_key)
    _INTRADAY_TTL[ttl_key] = (time.monotonic(), result)
    _INTRADAY_TTL.move_to_end(ttl_key)
    while len(_INTRADAY_TTL) > _INTRADAY_TTL_MAX:
        _INTRADAY_TTL.popitem(last=False)



def _shard_path(ym):
    import platform_env
    platform_env.ensure_dirs()
    return os.path.join(platform_env.CACHE_DIR, '_factorlab_kline_%s.pkl' % ym)


def _table_max_date(cfg_db, tbl):
    conn = PB.connect_replica_db(cfg_db)
    try:
        r = pd.read_sql(SQL.max_trade_date(tbl), conn)
    finally:
        conn.close()
    return pd.Timestamp(r['m'].iloc[0])


def _stock_rx_mask(df):
    """月片层只保留个股 rxcode：剔除指数（SH.000xxx 上证指数、SZ.399xxx 深证指数）。"""
    rx = df['rxcode']
    return ~((rx.str.startswith('SH.000')) | (rx.str.startswith('SZ.399')))


def _load_raw_month(ym, provider, cfg_db=None, refresh_ym=None, allow_db=True):
    """读一个全市场月片：磁盘分片优先；仅末端分片按需从数据源刷新。

    provider 无关：MySQL 经 PB.load_kline，API 经 provider.load_month，
    返回同构列并统一写本地分片，两种模式共用同一缓存、互不重复；
    import（本地 CSV）直接读源，不写共享月片缓存，保证导入数据即时生效。
    """
    if provider.name == 'import':
        # 本地导入 CSV：不经共享月片缓存，避免与 mysql/api 分片互相污染，
        # 也让新导入的文件每次回测都即时生效（本地解析开销很小）。
        df = provider.load_month(ym)
        df = _normalize_identity(df)
        df = df.loc[_stock_rx_mask(df)].reset_index(drop=True)
        return df
    path = _shard_path(ym)
    cached = None
    if os.path.exists(path):
        blob = cache_crypto.load_blob(path)
        if blob.get('version') == SHARD_VERSION:
            cached = _normalize_identity(blob['df'])
    if cached is not None and ym != refresh_ym:
        return cached
    need_read = cached is None
    if cached is not None and allow_db and provider.name == 'mysql':
        try:
            shard_max = pd.Timestamp(cached['trade_date'].max())
            if _table_max_date(cfg_db, 'stock_daily_' + ym) > shard_max:
                need_read = True
        except Exception:
            # 库不可用：离线复用已有分片，不报错
            need_read = False
    elif cached is not None and provider.name == 'api':
        # API 无法像 MySQL 那样按 MAX(trade_date) 廉价探测，末端月直接拉取
        need_read = True
    if not need_read:
        if cached is not None:
            return cached
        if not allow_db:
            raise RuntimeError('本地缺少 %s 的缓存分片且数据源不可用' % ym)
    t0 = time.time()
    if provider.name == 'mysql':
        df = PB.load_kline(cfg_db, set(), ['stock_daily_' + ym])
    else:
        df = provider.load_month(ym)
    df = _normalize_identity(df)
    df = df.loc[_stock_rx_mask(df)].reset_index(drop=True)
    if df.empty:
        # 空月片（如当月尚未产生行情）不落地，避免本地生成重复/空数据
        print('月片为空，不缓存: %s %.1fs' % (ym, time.time() - t0))
        return df
    cache_crypto.save_blob(path, {'version': SHARD_VERSION, 'df': df})
    print('月片已缓存: %s rows=%d %.1fs' % (path, len(df), time.time() - t0))
    return df


def load_raw_shards(provider, month_tables, cfg_db=None, allow_db=True,
                    cache_only=False):
    refresh_ym = None if cache_only else month_tables[-1].replace(
        'stock_daily_', '')
    frames = []
    for t in month_tables:
        ym = t.replace('stock_daily_', '')
        if cache_only:
            path = _shard_path(ym)
            if not os.path.exists(path):
                continue
            blob = cache_crypto.load_blob(path)
            if blob.get('version') != SHARD_VERSION:
                continue
            frames.append(_normalize_identity(blob['df']))
        else:
            frames.append(_load_raw_month(ym, provider, cfg_db=cfg_db,
                                          refresh_ym=refresh_ym,
                                          allow_db=allow_db))
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return df.sort_values(['rxcode', 'trade_date']).reset_index(drop=True)


def _codes_key(codes):
    import zlib
    norm = sorted(set(filter(None, (normalize_code(x) for x in codes))))
    return 'wl%x' % (zlib.crc32(','.join(norm).encode('utf-8')) & 0xffffffff)


def _local_shard_months():
    """列出本地缓存分片覆盖的 YYYYMM（升序）。"""
    import glob
    pat = os.path.join(platform_env.CACHE_DIR, '_factorlab_kline_*.pkl')
    out = []
    for p in glob.glob(pat):
        ym = os.path.basename(p)[len('_factorlab_kline_'):-len('.pkl')]
        if len(ym) == 6 and ym.isdigit():
            out.append(ym)
    return sorted(out)


def prune_local_cache(keep_months=6, dry_run=False):
    """按"自然月"裁剪本地行情月片，只保留最近 keep_months 个月。

    以本地最新月片所在月为基准往前数 keep_months 个月（含当月），
    早于该月的 ``_factorlab_kline_YYYYMM.pkl`` 删除。面板缓存
    （_factorlab_panel_*）若其区间起点落在被删月份之前则一并删除，
    避免下次读到引用已缺失月片的旧面板。
    返回 {'deleted_shards': [...], 'deleted_panels': [...],
          'kept_shards': [...], 'cutoff': 'YYYYMM'}。
    """
    import glob
    months = _local_shard_months()
    result = {'deleted_shards': [], 'deleted_panels': [],
              'kept_shards': [], 'cutoff': None}
    if not months:
        return result
    latest = pd.Period(months[-1], freq='M')
    cutoff = latest - (int(keep_months) - 1)
    cutoff_s = cutoff.strftime('%Y%m')
    result['cutoff'] = cutoff_s
    for ym in months:
        path = _shard_path(ym)
        if ym < cutoff_s:
            result['deleted_shards'].append(ym)
            if not dry_run:
                try:
                    os.remove(path)
                except OSError:
                    pass
        else:
            result['kept_shards'].append(ym)
    for p in glob.glob(os.path.join(
            platform_env.CACHE_DIR, '_factorlab_panel_*.pkl')):
        try:
            blob = cache_crypto.load_blob(p)
            cal = blob.get('cal')
            panel_start = pd.Timestamp(cal[0]).to_period('M')
        except Exception:
            continue
        if panel_start < cutoff:
            result['deleted_panels'].append(os.path.basename(p))
            if not dry_run:
                try:
                    os.remove(p)
                except OSError:
                    pass
    return result


def _month_range_tables(start, end):
    """枚举 [start-60自然日预热, end] 覆盖的全市场月表名（升序）。

    API 无法廉价探测库内已有月表，按时间范围生成；空月片不落地。
    """
    lo = (pd.Timestamp(start) - pd.Timedelta(days=60)).to_period('M')
    hi = pd.Timestamp(end).to_period('M')
    out = []
    cur = lo
    while cur <= hi:
        out.append('stock_daily_' + cur.strftime('%Y%m'))
        cur += 1
    return out


def _build_custom_universe(base_universe, custom_codes):
    """自定义股票池：按唯一 rxcode 精确匹配，缺失的以占位行补齐。"""
    wanted_rx = {PB.to_rxcode(PB.to_full(c))
                 for c in (normalize_code(x) for x in custom_codes) if c}
    universe = base_universe[base_universe['rxcode'].isin(wanted_rx)].copy()
    miss = sorted(wanted_rx - set(universe['rxcode']))
    if miss:
        universe = pd.concat([universe, pd.DataFrame(
            [dict(rxcode=rx, code=rx.split('.', 1)[-1], name=rx,
                  list_date=pd.NaT, is_st=0) for rx in miss])],
            ignore_index=True)
    return universe


PANEL_DISK_VERSION = 1


def _panel_disk_key(memo_key, start, end):
    import hashlib
    raw = repr((memo_key, pd.Timestamp(start).value,
                pd.Timestamp(end).value))
    return hashlib.md5(raw.encode('utf-8')).hexdigest()[:16]


def _panel_disk_path(disk_key):
    import platform_env
    platform_env.ensure_dirs()
    return os.path.join(platform_env.CACHE_DIR,
                        '_factorlab_panel_%s.pkl' % disk_key)


def _load_panel_disk(memo_key, start, end):
    path = _panel_disk_path(_panel_disk_key(memo_key, start, end))
    if not os.path.exists(path):
        return None
    try:
        blob = cache_crypto.load_blob(path)
    except Exception:
        return None
    if blob.get('version') != PANEL_DISK_VERSION:
        return None
    return blob['cal'], blob['panels']


def _save_panel_disk(memo_key, start, end, cal, panels):
    path = _panel_disk_path(_panel_disk_key(memo_key, start, end))
    try:
        t0 = time.time()
        cache_crypto.save_blob(path, {'version': PANEL_DISK_VERSION,
                                      'cal': cal, 'panels': panels})
        print('面板已缓存到磁盘: %s %.1fs' % (path, time.time() - t0))
    except Exception as e:
        print('面板磁盘缓存失败: %s' % e)


def _assemble_panels(memo_key, kline_all, universe, start, end, nproc):
    # 按唯一 rxcode 过滤，避免重复 code 误纳指数/同号个股
    kline = kline_all[kline_all['rxcode'].isin(set(universe['rxcode']))] \
        .reset_index(drop=True)
    end_ts = min(kline['trade_date'].max(), end)
    kline = kline[kline['trade_date'] <= end_ts].reset_index(drop=True)
    cal = np.sort(kline['trade_date'].unique())
    cal = [pd.Timestamp(x) for x in cal if start - pd.Timedelta(days=60)
           <= pd.Timestamp(x) <= end_ts]
    panels = build_panels(kline, universe, nproc)
    _PANEL_MEMO[memo_key] = (cal, panels)
    _PANEL_MEMO.move_to_end(memo_key)
    while len(_PANEL_MEMO) > _PANEL_MEMO_MAX:
        _PANEL_MEMO.popitem(last=False)
    return cal, panels


def _fallback_month_tables(start, end):
    """数据源鉴权/连接失败时：枚举落在请求区间内的本地分片月表。

    返回升序 stock_daily_YYYYMM 列表；本地无任何分片时返回 None。
    """
    local_ym = _local_shard_months()
    if not local_ym:
        return None
    s_ym = (start - pd.Timedelta(days=60)).strftime('%Y%m')
    e_ym = end.strftime('%Y%m')
    tables = ['stock_daily_' + ym for ym in local_ym if s_ym <= ym <= e_ym]
    return tables or None


def load_or_build(tag, user_id, start, end, nproc, rebuild=False,
                  read_config=None, boards=None, exclude_st=False,
                  exclude_new_days=None, custom_codes=None,
                  panel_version=None, provider=None,
                  disk_persist=False):
    version = PANEL_VERSION if panel_version is None else int(panel_version)
    cfg_db = read_config or PB.READ_CONFIG
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    memo_key = (tag, version, tuple(boards) if boards else None,
                bool(exclude_st), exclude_new_days,
                None if custom_codes is None else _codes_key(custom_codes))
    if (not rebuild) and memo_key in _PANEL_MEMO:
        _PANEL_MEMO.move_to_end(memo_key)
        print('使用内存面板:', tag)
        cal, panels = _PANEL_MEMO[memo_key]
        return cal, panels, None
    if (not rebuild) and disk_persist:
        disk_hit = _load_panel_disk(memo_key, start, end)
        if disk_hit is not None:
            cal, panels = disk_hit
            _PANEL_MEMO[memo_key] = (cal, panels)
            _PANEL_MEMO.move_to_end(memo_key)
            while len(_PANEL_MEMO) > _PANEL_MEMO_MAX:
                _PANEL_MEMO.popitem(last=False)
            print('使用磁盘面板:', tag)
            return cal, panels, None

    # 统一主干：无论 MySQL 还是 API，都加载同一套全市场月片（本地分片优先），
    # 再按唯一 rxcode 组装面板，避免重复取数与重复落地。
    if provider is None:
        from data_provider import MysqlProvider as _MP
        provider = _MP(cfg_db)

    offline = False
    degraded = None
    universe = None
    if provider.name in ('api', 'import'):
        # API/import 模式绝不连接本地 MySQL：股票池来自接口或本地导入文件。
        # 数据源不可用（Key 失效 401/403、超时、网络错误、未配置令牌、
        # 依赖缺失等任何异常）均不阻断历史回测：降级到本地缓存分片，
        # universe 稍后从行情反推；本地无任何缓存时才抛错。import 为本地
        # 文件源基本不会出错，但仍走同一分支以复用 load_raw_shards 主干。
        try:
            base_universe, _ = provider.load_universe(
                None if custom_codes is not None else user_id)
            if custom_codes is not None:
                universe = _build_custom_universe(base_universe, custom_codes)
            else:
                universe = filter_universe(
                    base_universe, boards=boards, exclude_st=exclude_st,
                    min_list_days=exclude_new_days, asof=end)
        except Exception as src_err:
            fallback = _fallback_month_tables(start, end)
            if fallback is None:
                raise src_err
            offline = True
            degraded = str(src_err)
            universe = None
            month_tables = fallback
    else:
        # MySQL 模式：先在线取股票池和月表；库不可用则退回本地缓存分片。
        try:
            rconn = PB.connect_replica_db(cfg_db)
            try:
                base_universe, _ = PB.load_universe(rconn)
                if custom_codes is not None:
                    universe = _build_custom_universe(
                        base_universe, custom_codes)
                else:
                    universe = filter_universe(
                        base_universe, boards=boards, exclude_st=exclude_st,
                        min_list_days=exclude_new_days, asof=end)
                month_tables = PB.discover_month_tables(rconn, start, end)
            finally:
                rconn.close()
        except Exception as db_err:
            # 仅当本地确有可用缓存时静默离线；否则把错误交给真正要读数据的环节
            fallback = _fallback_month_tables(start, end)
            if fallback is None:
                raise db_err
            offline = True
            month_tables = fallback
            universe = None  # 先读分片，再从行情里推导股票池
            degraded = ('直连 MySQL 取数失败，已改用本地缓存行情，数据区间可能被截断。'
                        '原因：%s' % db_err)

    if provider.name in ('api', 'import'):
        month_tables = _month_range_tables(start, end)

    print('正在加载 %d 只股票的行情数据...' %
          (len(universe) if universe is not None else 0))
    if provider.name in ('api', 'import'):
        try:
            kline_all = load_raw_shards(provider, month_tables, cfg_db=cfg_db,
                                        allow_db=not offline,
                                        cache_only=offline)
        except Exception as src_err:
            # universe 已取到、末端月拉取时数据源/依赖不可用：退回本地缓存分片
            fallback = _fallback_month_tables(start, end)
            if fallback is None:
                raise src_err
            offline = True
            degraded = str(src_err)
            universe = None
            month_tables = fallback
            kline_all = load_raw_shards(
                provider, month_tables, cfg_db=cfg_db, allow_db=False,
                cache_only=True)
    else:
        kline_all = load_raw_shards(provider, month_tables, cfg_db=cfg_db,
                                    allow_db=not offline)
    if offline and universe is None:
        print('数据源连接失败，改用本地缓存行情...')
        meta = (kline_all.sort_values('trade_date')
                .groupby('rxcode')
                .agg(code=('code', 'last'), name=('name', 'last'),
                     is_st=('is_st', 'max'))
                .reset_index())
        universe = pd.DataFrame({
            'rxcode': meta['rxcode'], 'code': meta['code'],
            'name': meta['name'],
            'list_date': pd.NaT, 'is_st': meta['is_st']})
        if custom_codes is not None:
            wanted_rx = {PB.to_rxcode(PB.to_full(c))
                         for c in (normalize_code(x) for x in custom_codes) if c}
            universe = universe[universe['rxcode'].isin(wanted_rx)]
        universe = filter_universe(
            universe, boards=boards, exclude_st=exclude_st,
            min_list_days=None, asof=end)
    cal, panels = _assemble_panels(
        memo_key, kline_all, universe, start, end, nproc)
    if disk_persist:
        _save_panel_disk(memo_key, start, end, cal, panels)
    if degraded is None and offline:
        degraded = ('数据源连接失败，已改用本地缓存的历史数据完成回测，'
                    '数据区间可能被截断。')
    return cal, panels, degraded


def _approx_limit_up(code, pre_close, is_st):
    """按板块用昨收近似涨停价（仅过滤用，不参与信号）。"""
    pc = pd.to_numeric(pd.Series(pre_close), errors='coerce')
    factor = pd.Series(1.1, index=pc.index)
    c = pd.Series(code).astype(str)
    grow = c.str.startswith(('300', '301', '302', '688', '689'))
    factor = factor.where(~grow, 1.2)
    if is_st is not None:
        st = pd.to_numeric(pd.Series(is_st), errors='coerce').fillna(0).astype(int) == 1
        factor = factor.where(~st, 1.05)
    return (pc * factor).where(pc > 0)


def load_or_build_intraday(tag, user_id, start, nproc, rebuild=False,
                           read_config=None, boards=None, exclude_st=False,
                           exclude_new_days=None, custom_codes=None,
                           panel_version=None, provider=None, day=None,
                           force=False, ttl_seconds=None):
    """盘中口径：历史日线月片 + instant 当天快照，整体重算面板。

    返回 (cal, panels, {'trade_date', 'tick_time', 'n_snapshot'})；
    只写独立的 _INTRADAY_MEMO，不写任何日线月片 pkl。
    ttl_seconds：>0 时，窗口内复用上次结果、完全不发 API 请求；None 读配置（默认 60s）；0 关闭。
    force=True 时绕过 TTL 与 ETag 条件请求，强制拉取最新快照。
    """
    version = PANEL_VERSION if panel_version is None else int(panel_version)
    cfg_db = read_config or PB.READ_CONFIG
    if provider is None:
        from data_provider import MysqlProvider as _MP
        provider = _MP(cfg_db)

    pool_key = None if custom_codes is None else _codes_key(custom_codes)
    req_day = None if day is None else pd.Timestamp(day).strftime('%Y-%m-%d')
    ttl_key = ('intraday', version, tuple(boards) if boards else None,
               bool(exclude_st), exclude_new_days, pool_key, req_day)
    if ttl_seconds is None:
        ttl_seconds = get_intraday_ttl_seconds()
    ttl_seconds = int(ttl_seconds)
    if (not force) and ttl_seconds > 0 and ttl_key in _INTRADAY_TTL:
        fetched_at, cached = _INTRADAY_TTL[ttl_key]
        if time.monotonic() - fetched_at < ttl_seconds:
            _INTRADAY_TTL.move_to_end(ttl_key)
            print('使用盘中 TTL 结果（%ds 内不重复请求）' % ttl_seconds)
            return cached

    snap_df, snap_date, tick_time = provider.intraday_snapshot(day, force=force)
    if snap_df is None or snap_df.empty or snap_date is None:
        raise RuntimeError('盘中快照尚未生成，无法执行盘中选股')
    snap_day = pd.Timestamp(snap_date)
    snap_df = snap_df[snap_df['trade_date'] == snap_day].copy()
    snap_df = snap_df.drop_duplicates(subset=['rxcode'], keep='last')

    memo_key = ('intraday', version, tuple(boards) if boards else None,
                bool(exclude_st), exclude_new_days, pool_key,
                snap_day.strftime('%Y-%m-%d'), str(tick_time))
    if (not rebuild) and memo_key in _INTRADAY_MEMO:
        _INTRADAY_MEMO.move_to_end(memo_key)
        print('使用盘中内存面板:', snap_day.date())
        result = _INTRADAY_MEMO[memo_key]
        _store_intraday_ttl(ttl_key, result, ttl_seconds, force)
        return result

    base_universe, _ = provider.load_universe(
        None if custom_codes is not None else user_id)
    if custom_codes is not None:
        universe = _build_custom_universe(base_universe, custom_codes)
    else:
        universe = filter_universe(
            base_universe, boards=boards, exclude_st=exclude_st,
            min_list_days=exclude_new_days, asof=snap_day)

    hist_end = snap_day - pd.Timedelta(days=1)
    start = pd.Timestamp(start)
    is_db = provider.name == 'mysql'
    if is_db:
        # 直连模式：只枚举库内真实存在的月表，避免对不存在的表发查询
        rconn = PB.connect_replica_db(cfg_db)
        try:
            month_tables = PB.discover_month_tables(rconn, start, snap_day)
        finally:
            rconn.close()
    else:
        month_tables = _month_range_tables(start, snap_day)
    print('正在加载当日历史行情 %s ~ %s ...' %
          (start.date(), hist_end.date()))
    kline_all = load_raw_shards(provider, month_tables, cfg_db=cfg_db,
                                allow_db=is_db, cache_only=not is_db)
    kline_all = kline_all[kline_all['trade_date'] < snap_day]
    kline_all = kline_all[kline_all['trade_date']
                          >= start - pd.Timedelta(days=60)]

    snap = snap_df.copy()
    snap['limit_up_price'] = _approx_limit_up(
        snap['code'].values, snap['pre_close_price'].values,
        snap['is_st'].values).values
    snap['limit_up'] = snap['limit_up_price']
    # 必须保留 open/high/low/close 别名：calc_breitstein 读取这四列（而非 *_price）；
    # 历史月片带这些列，快照若丢掉会在末根拼出 NaN，导致 Breitstein 指标末根失效。
    keep = ['rxcode', 'code', 'name', 'trade_date', 'open_price',
            'high_price', 'low_price', 'close_price', 'open', 'high',
            'low', 'close', 'volume', 'limit_up_price', 'limit_up', 'is_st']
    snap = snap.reindex(columns=keep)
    kline_all = pd.concat([kline_all, snap], ignore_index=True)
    kline_all = kline_all.sort_values(['rxcode', 'trade_date']).reset_index(drop=True)

    kline = kline_all[kline_all['rxcode'].isin(set(universe['rxcode']))] \
        .reset_index(drop=True)
    end_ts = snap_day
    cal = np.sort(kline['trade_date'].unique())
    cal = [pd.Timestamp(x) for x in cal if start - pd.Timedelta(days=60)
           <= pd.Timestamp(x) <= end_ts]
    if snap_day not in cal:
        cal.append(snap_day)
        cal.sort()
    panels = build_panels(kline, universe, nproc)
    result = (cal, panels,
              {'trade_date': snap_day.strftime('%Y-%m-%d'),
               'tick_time': tick_time, 'n_snapshot': len(snap)})
    _INTRADAY_MEMO[memo_key] = result
    _INTRADAY_MEMO.move_to_end(memo_key)
    while len(_INTRADAY_MEMO) > _INTRADAY_MEMO_MAX:
        _INTRADAY_MEMO.popitem(last=False)
    _store_intraday_ttl(ttl_key, result, ttl_seconds, force)
    return result


# ==================== 层4：Breitstein 双管道单计划撮合引擎 ====================

# exit_reason 优先级链（数字小者优先，spec FR-7 平台适配口径）：
# 固定止损 ＞ 结构/AVWAP 止损 ＞ ATR 止损 ＞ 趋势破坏 ＞ 移动止盈 ＞
# 共振走弱 ＞ 活跃度衰减 ＞ 到期
_EXIT_RANK = {
    'stop': 0, 'struct': 1, 'atr': 2,
    'trend': 3, 'trail': 4,
    'res_weak': 5, 'act_decay': 6, 'time': 7,
}


def _open_plan(rx, p, i, t, opportunity, weight, stop_price, res_at_entry):
    """新建单一持仓计划（单计划、单均价、无批次）。
    记录 entry 时点信息：entry_opportunity / weight / stop_price /
    resonance_at_entry / exposure_start，供明细与出场锚定使用。
    """
    return {
        'rxcode': rx, 'code': p['code'], 'name': p['name'],
        'start': t, 'start_i': i,
        'entry_date': t, 'entry_i': i,
        'entry_opportunity': opportunity,
        'entry_price': float(p['close'][i]),
        'weight': float(weight),
        'stop_price': float(stop_price),
        'resonance_at_entry': float(res_at_entry),
        'exposure_start': i,
        '_prev_close': float(p['close'][i]),
        '_peak_hi': float(p['close'][i]),  # 持仓期盘中最高价，用于移动止盈回撤判定
        '_decay': 0,
    }


def _close_row(rx, code, name, plan, t, px, reason, fee, fee_legs=2):
    """结算单一持仓，返回一条交易明细记录（无 batch_no）。"""
    ep = plan['entry_price']
    gross = float(px) / ep - 1.0 if ep > 0 else 0.0
    net = gross - fee * fee_legs
    return {
        'rxcode': rx, 'code': code, 'name': name,
        'entry_date': plan['entry_date'],
        'entry_event': plan['entry_opportunity'],
        'entry_opportunity': plan['entry_opportunity'],
        'entry_price': ep, 'weight': plan['weight'],
        'exit_date': t, 'exit_price': float(px),
        'exit_reason': reason,
        'resonance_at_entry': plan['resonance_at_entry'],
        'exposure_start': plan['exposure_start'],
        'ret': net,
    }


def _anchor_stop(opportunity, p, i, buf):
    """平台适配止损口径（Breitstein 原文未定义，spec FR-7 标注非忠实映射项）：
    - opportunity=='reversal'（投降+收复 AVWAP）：若 entry 当日 close 已收复 AVWAP
      （close>avwap），以收复的 AVWAP 为锚，stop=avwap*(1-buf)（跌破收复的 AVWAP 出场）；
      否则以投降 K 线当日低点为锚，stop=entry_low*(1-buf)。面板有 capitulation_bar 时
      可近似取 min(entry_low, 最近支撑)。
    - 其余（breakout）：以 entry 当日低点为锚，stop=entry_low*(1-buf)。
    无法取锚时兜底 entry close*(1-0.08)。
    """
    def _pick(k):
        a = p.get(k)
        if a is not None and i < len(a):
            v = float(a[i])
            if np.isfinite(v) and v > 0:
                return v
        return None

    lo = _pick('low')
    cl = _pick('close')
    if opportunity == 'reversal':
        av = _pick('avwap')
        if av is not None and cl is not None and cl > av:
            return av * (1.0 - buf)
    if lo is not None:
        return lo * (1.0 - buf)
    base = cl if cl is not None else 1.0
    return base * (1.0 - 0.08)


def simulate(cal, panels, cfg, seg_start=None, seg_end=None, mask_cache=None,
             collect_equity=False, collect_detail=False, fee_bps=0.0):
    """Breitstein 双管道单计划撮合：在 [seg_start, seg_end] 信号区间重放。

    A 股单向做多；T 日收盘信号按当日收盘价成交（涨停日不买）。
    管道 A（盘整突破，breakout）与管道 B（情绪反转，reversal）并行；同一标的
    单计划、单均价、无批次。仓位 target = (1/topn)*position_mult，当日所有新开
    权重和 >100% 时等比归一至 100%，单票封顶 max_weight。
    出场为平台适配优先级链（exit_enabled 可整体关闭）。

    cfg 键（均带默认，后续由 base_cfg 统一提供）：
      topn/initial_capital/activity_th/topn_cap/conf_th
      pivot_order/conf_window/touch_tol
      comp_window/hist_window/pct_th/z_th
      drift_th/slope_th/cv_th/cv_day_ratio
      roc_n/roc_min
      pipeline_b_emotion_th/capitulate_vol_mult/emotion_extreme_th/max_weight
      allow_bear/min_list_days/exclude_st
      stop_loss/atr_exit/atr_mult/avwap_stop_buf
      cons_weak_exit/cons_weak_th/activity_decay_exit/activity_decay_days/max_hold
      exit_enabled

    返回：collect_equity=False 时 trades DataFrame；
      True 时 (trades_df, eq_df, info)；collect_detail=True 时再附 detail_rows。
    """
    seg_start = pd.Timestamp(seg_start) if seg_start is not None else pd.Timestamp(cal[0])
    seg_end = pd.Timestamp(seg_end) if seg_end is not None else pd.Timestamp(cal[-1])
    fee = fee_bps / 10000.0
    cal_idx = {pd.Timestamp(x): i for i, x in enumerate(cal)}

    topn = int(cfg.get('topn', 10))
    initial_capital = float(cfg.get('initial_capital', 1_000_000))
    activity_th = float(cfg.get('activity_th', 1.5))
    conf_th = float(cfg.get('conf_th', 0.10))
    conf_window = int(cfg.get('conf_window', 100))
    touch_tol = float(cfg.get('touch_tol', 0.002))
    comp_window = int(cfg.get('comp_window', 10))
    pct_th = float(cfg.get('pct_th', 0.10))
    z_th = float(cfg.get('z_th', -1.0))
    roc_min = float(cfg.get('roc_min', 0.01))
    pipeline_b_emotion_th = float(cfg.get('pipeline_b_emotion_th', 2.0))
    # 入场管线开关：breakout=仅管道A(盘整突破)；reversal=仅管道B(情绪反转)；
    # both=双管道并行（默认）。决定后面管道B/A 扫描块是否执行。
    pipeline = str(cfg.get('pipeline') or 'both').strip().lower()
    # 逐条条件开关（默认全开=与原先全 AND 一致；任一关闭则该条件不再拦截）
    use_activity = bool(cfg.get('use_activity', True))
    use_clean_level = bool(cfg.get('use_clean_level', True))
    use_breakout = bool(cfg.get('use_breakout', True))
    use_compression = bool(cfg.get('use_compression', True))
    use_roc = bool(cfg.get('use_roc', True))
    use_emotion = bool(cfg.get('use_emotion', True))
    use_reversal_entry = bool(cfg.get('use_reversal_entry', True))
    use_resonance = bool(cfg.get('use_resonance', True))
    max_weight = float(cfg.get('max_weight', 0.20))
    allow_bear = bool(cfg.get('allow_bear', False))
    min_list_days = int(cfg.get('min_list_days', 60))
    exclude_st = bool(cfg.get('exclude_st', True))
    stop_loss = cfg.get('stop_loss', None)
    stop_loss = float(stop_loss) if stop_loss is not None else None
    atr_on = bool(cfg.get('atr_exit', False))
    atr_mult = float(cfg.get('atr_mult', 3.0))
    avwap_stop_buf = float(cfg.get('avwap_stop_buf', 0.02))
    cons_weak_exit = bool(cfg.get('cons_weak_exit', True))
    cons_weak_th = float(cfg.get('cons_weak_th', -2))
    act_decay_exit = bool(cfg.get('activity_decay_exit', False))
    act_decay_days = int(cfg.get('activity_decay_days', 3))
    max_hold = cfg.get('max_hold', None)
    max_hold = int(max_hold) if max_hold is not None else None
    exit_enabled = bool(cfg.get('exit_enabled', True))
    # 移动止盈 / 趋势破坏出场（平台适配，解决“持有过久、高位回吐才被清”）
    trail_exit = bool(cfg.get('trail_exit', False))
    trail_pct = float(cfg.get('trail_pct', 0.10))        # 峰值回撤阈值
    trail_min_gain = float(cfg.get('trail_min_gain', 0.03))  # 触发移动止盈所需最低浮盈
    trend_exit = bool(cfg.get('trend_exit', False))
    trend_buf = float(cfg.get('trend_buf', 0.0))         # 跌破 AVWAP 缓冲
    # 管道 B 入场：盘整/压缩前置（避免在未盘整的放量追高形态入场）
    use_compression_b_pre = bool(cfg.get('use_compression_b_pre', False))
    comp_b_pct_th = float(cfg.get('comp_b_pct_th', 0.12))

    def _f(val):
        """取有限浮点，NaN/None 返回 None。"""
        try:
            v = float(val)
            return v if np.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    def _fv(p, k, i):
        """面板数组取值：缺失/越界/非有限返回 None。"""
        a = p.get(k)
        if a is None or i is None or i < 0 or i >= len(a):
            return None
        return _f(a[i])

    # 管道A / 突破箱顶：因果重算"前一根已定箱顶"（high.rolling(comp).max().shift(1)），
    # 当日突破柱不算入箱顶，与 breitstein_screen 同语义、无未来函数。
    box_hi_cache = {}
    for rx, p in panels.items():
        hi = p.get('high')
        if hi is None:
            box_hi_cache[rx] = None
            continue
        s = pd.Series(hi, dtype='float64')
        box_hi_cache[rx] = s.rolling(comp_window).max().shift(1).values

    plans = {}            # rx -> 活跃单持仓计划
    trades = []
    equity_rows = []
    detail_rows = []
    n_entries = 0
    eq_hist = []
    for t in cal:
        t = pd.Timestamp(t)
        in_seg = seg_start <= t <= seg_end

        # 当日损益两段：退出持仓（昨收 -> 成交价）、存活持仓（昨收 -> 今收）
        realized = []
        mark_items = []
        fee_legs = 0.0
        opened = []       # 当日新开（用于归一化）

        # ---- 1. 退出评估：优先级链，逐持仓取链首（entry 当日不做出场）----
        exit_hits = {}    # rx -> (reason, px)
        if exit_enabled:
            for rx, plan in list(plans.items()):
                p = panels[rx]
                i = p['pos'].get(t)
                if i is None:
                    continue
                if plan['entry_i'] >= i:
                    continue
                lo = _fv(p, 'low', i)
                op = _fv(p, 'open', i)
                cp = _fv(p, 'close', i)
                if cp is None or cp <= 0:
                    continue
                ep = plan['entry_price']
                cands = []
                # 固定止损（盘中 low 判价，同 bar 先止损后止盈）
                if stop_loss is not None and lo is not None and ep > 0 \
                        and lo / ep - 1.0 <= -stop_loss:
                    px = ep * (1.0 - stop_loss)
                    if op is not None:
                        px = min(px, op)
                    cands.append(('stop', px))
                # 结构/AVWAP 止损（跌破锚定价）
                if lo is not None and lo <= plan['stop_price']:
                    px = plan['stop_price']
                    if op is not None:
                        px = min(px, op)
                    cands.append(('struct', px))
                # ATR 止损：锚定 entry 当日已确认 ATR；面板无 atr 列则本档自动失效
                if atr_on:
                    a = _fv(p, 'atr', plan['entry_i']) if 'atr' in p else None
                    if a and ep > 0:
                        sa = ep - atr_mult * a
                        if lo is not None and lo <= sa:
                            px = sa
                            if op is not None:
                                px = min(px, op)
                            cands.append(('atr', px))
                # 趋势/AVWAP 破坏出场（当日收盘跌破 AVWAP，带缓冲；面板无 avwap 则自动失效）
                if trend_exit:
                    av = _fv(p, 'avwap', i)
                    if av is not None and av > 0 and cp <= av * (1.0 - trend_buf):
                        cands.append(('trend', cp))
                # 移动止盈/回撤出场：峰值追踪，浮盈达标后收盘回撤触发
                if trail_exit:
                    hi = _fv(p, 'high', i)
                    if hi is not None and hi > plan['_peak_hi']:
                        plan['_peak_hi'] = hi
                    peak_in_gain = plan['_peak_hi'] / ep - 1.0 if ep > 0 else 0.0
                    if peak_in_gain >= trail_min_gain \
                            and cp <= plan['_peak_hi'] * (1.0 - trail_pct):
                        cands.append(('trail', cp))
                # 共振走弱（当日收盘触发；入场 res>=0 与出场 res<=cons_weak_th 自洽）
                if cons_weak_exit:
                    res = _fv(p, 'resonance', i)
                    if res is not None and res <= cons_weak_th:
                        cands.append(('res_weak', cp))
                # 活跃度衰减（当日收盘触发，连续计数）
                if act_decay_exit:
                    vr = _fv(p, 'volume_ratio', i)
                    ar = _fv(p, 'atr_ratio', i)
                    if vr is not None and ar is not None and vr < 1.0 and ar < 1.0:
                        plan['_decay'] += 1
                    else:
                        plan['_decay'] = 0
                    if plan['_decay'] >= act_decay_days:
                        cands.append(('act_decay', cp))
                # 到期
                if max_hold is not None and i - plan['entry_i'] >= max_hold:
                    cands.append(('time', cp))
                if cands:
                    exit_hits[rx] = min(cands, key=lambda c: _EXIT_RANK[c[0]])

        # ---- 2. 结算退出持仓（一次性清仓）----
        for rx, (reason, px) in exit_hits.items():
            plan = plans.pop(rx)
            # 平台适配：跌停日难成交，此处以止损/结构锚定价（已 min 开盘）成交；若跌停
            # 无法判定，以 close 近似并注释——绝不凭空新增成交，出场仅由真实 low/close 触发。
            pc = plan['_prev_close']
            if pc and pc > 0 and px:
                realized.append({'rx': rx, 'code': plan['code'],
                                 'name': plan['name'], 'w': plan['weight'],
                                 'ret': px / pc - 1.0})
            trades.append(_close_row(rx, plan['code'], plan['name'],
                                     plan, t, px, reason, fee, fee_legs=1))
            fee_legs += plan['weight']

        # ---- 3. 存活持仓 mark-to-market（昨收 -> 今收）----
        for rx, plan in list(plans.items()):
            p = panels[rx]
            i = p['pos'].get(t)
            if i is None:
                continue
            cp = _fv(p, 'close', i)
            if cp is None or cp <= 0:
                continue
            pc = plan['_prev_close']
            if pc and pc > 0:
                mark_items.append({'rx': rx, 'code': plan['code'],
                                   'name': plan['name'], 'w': plan['weight'],
                                   'ret': cp / pc - 1.0})
            plan['_prev_close'] = cp

        # ---- 4. 双管道入场扫描（当日候选按 resonance 降序占用槽位）----
        if in_seg:
            cands = []
            for rx, p in panels.items():
                if rx in plans:
                    continue
                i = p['pos'].get(t)
                if i is None:
                    continue
                cp = _fv(p, 'close', i)
                if cp is None or cp <= 0:
                    continue
                # ST / 最小上市时长过滤
                st = p.get('is_st')
                if exclude_st and st is not None and i < len(st) and int(st[i]) == 1:
                    continue
                ld = p.get('list_date')
                if ld is not None and (t - pd.Timestamp(ld)).days < min_list_days:
                    continue
                # 涨停不买
                lu = _fv(p, 'limit_up', i)
                if lu and cp >= lu * 0.999:
                    continue
                # 多空偏置：默认仅 resonance>=0 放行做多；关闭要点五开关则不拦截
                res = _fv(p, 'resonance', i)
                if use_resonance and not allow_bear and (res is None or res < 0):
                    continue
                pm = _fv(p, 'position_mult', i)
                if pm is None:
                    pm = 1.0

                act = _fv(p, 'activity_raw', i)
                act_ok = _active(p, i, cfg) if use_activity else True
                opp = None

                # ---- 管道 B：情绪反转（投降+收复AVWAP）----
                er_arr = p.get('entry_reversal')
                er_true = bool(er_arr[i]) if er_arr is not None and i < len(er_arr) else False
                conf_s = _fv(p, 'level_conf_s', i)
                lev_s = _fv(p, 'level_support', i)
                emo = _fv(p, 'emotion_score', i)
                # 支撑存活验证：入场时支撑必须仍未失守（close 仍站上最近支撑），
                # 否则说明该支撑已被击穿，置信度再高也不应用于多单锚点
                support_alive = lev_s is None or (cp is not None and cp > lev_s)
                # 管道 B 盘整/压缩前置：若开启，要求入场前一根已处压缩态（对 RANGE_PCT/
                # RANGE_Z 之一判小），避免在未盘整的放量追高形态（close≈H）入场
                comp_b_ok = not use_compression_b_pre
                if not comp_b_ok:
                    _rp1 = _fv(p, 'range_pct', i - 1)
                    _rz1 = _fv(p, 'range_z', i - 1)
                    comp_b_ok = (_rp1 is not None and _rp1 < comp_b_pct_th) \
                        or (_rz1 is not None and _rz1 < z_th)
                if (pipeline != 'breakout'
                        and (not use_activity or act_ok)
                        and (not use_clean_level
                             or (conf_s is not None and conf_s > conf_th and support_alive))
                        and (not use_emotion
                             or (emo is not None and abs(emo) >= pipeline_b_emotion_th))
                        and (not use_reversal_entry or er_true)
                        and comp_b_ok):
                    opp = 'reversal'

                # ---- 管道 A：盘整突破（因果重算箱顶，与 breitstein_screen 同语义）----
                if opp is None and pipeline != 'reversal':
                    bhi = box_hi_cache.get(rx)
                    if bhi is not None and i < len(bhi):
                        bh = _f(bhi[i])
                    else:
                        bh = None
                    breakdown_ok = bool(bh) and bool(cp > bh)
                    rp1 = _fv(p, 'range_pct', i - 1)
                    rz1 = _fv(p, 'range_z', i - 1)
                    comp = (rp1 is not None and rp1 < pct_th) \
                        or (rz1 is not None and rz1 < z_th)
                    roc = _fv(p, 'breakout_roc', i)
                    roc_ok = roc is not None and roc > roc_min
                    conf_r = _fv(p, 'level_conf_r', i)
                    clean_ok = conf_r is not None and conf_r > conf_th
                    if ((not use_activity or act_ok)
                            and (not use_breakout or breakdown_ok)
                            and (not use_compression or comp)
                            and (not use_roc or roc_ok)
                            and (not use_clean_level or clean_ok)):
                        opp = 'breakout'

                if opp is not None:
                    w_raw = (1.0 / max(topn, 1)) * pm
                    cands.append((res if res is not None else -1e9, rx, p, i,
                                  cp, opp, w_raw))

            cands.sort(key=lambda c: (-c[0], c[1]))
            for resk, rx, p, i, cp, opp, w_raw in cands:
                if len(opened) >= topn:
                    break
                stop_px = _anchor_stop(opp, p, i, avwap_stop_buf)
                plan = _open_plan(rx, p, i, t, opp, w_raw, stop_px,
                                  resk if resk != -1e9 else 0.0)
                opened.append(plan)
                plans[rx] = plan
                n_entries += 1

            # 归一化：当日新开总权重 >100% 等比缩放至 100%；再封顶单票 max_weight
            total_w = sum(pl['weight'] for pl in opened)
            if total_w > 1.0:
                scale = 1.0 / total_w
                for pl in opened:
                    pl['weight'] *= scale
            for pl in opened:
                if pl['weight'] > max_weight:
                    pl['weight'] = max_weight
                plans[pl['rxcode']]['weight'] = pl['weight']
                fee_legs += pl['weight']

        # ---- 5. 组合日净值：昨收->今收 加权复利，费用随成交扣减 ----
        if collect_equity and in_seg:
            port_ret = 0.0
            for m in realized:
                port_ret += m['w'] * m['ret']
            for m in mark_items:
                port_ret += m['w'] * m['ret']
            port_ret -= fee * fee_legs
            prev_eq = eq_hist[-1] if eq_hist else 1.0
            eqv = prev_eq * (1.0 + port_ret)
            eq_hist.append(eqv)
            equity_rows.append({'date': t, 'port_ret': port_ret, 'equity': eqv,
                                'n_held': len(plans)})
            if collect_detail:
                for m in realized + mark_items:
                    detail_rows.append({'date': t, 'code': m['code'],
                                        'name': m['name'],
                                        'ret': m['w'] * m['ret']})

    # ---- 区间结束：未平仓按 seg_end 收盘价估值为 open（单边费），浮动盈亏计入净值 ----
    for rx, plan in list(plans.items()):
        p = panels[rx]
        i = p['pos'].get(seg_end)
        if i is None:
            i = p['pos'].get(cal[-1])
        if i is None:
            continue
        cp = _fv(p, 'close', i)
        if cp is None:
            continue
        sd = pd.Timestamp(p['dt'][i])
        trades.append(_close_row(rx, plan['code'], plan['name'],
                                 plan, sd, cp, 'open', fee, fee_legs=1))

    info = {'n_entries': n_entries}
    if collect_equity:
        eq_df = pd.DataFrame(equity_rows)
        if len(eq_df):
            eq = eq_df['equity'].values
            peak = np.maximum.accumulate(eq)
            eq_df['dd'] = (eq / peak - 1.0)
        else:
            eq_df['dd'] = []
        if collect_detail:
            return pd.DataFrame(trades), eq_df, info, detail_rows
        return pd.DataFrame(trades), eq_df, info
    return pd.DataFrame(trades)


# ==================== 层5：Breitstein 实验编排 ====================

def base_cfg(**kw):
    """Breitstein 五要点回测参数全集（与 simulate 读取的 cfg 键完全对应）。

    组成两段：
      1) breitstein.default_params() 的全部指标阈值键（活跃度/干净价位/盘整/情绪/多周期共振 + 综合），
         其中 activity_th/topn/conf_window/conf_th/touch_tol/pipeline_b_emotion_th/
         capitulate_vol_mult 等即 simulate 读取的键；
      2) 平台适配键（breitstein.default_params 中没有）：initial_capital/max_weight/
         allow_bear/min_list_days/exclude_st/stop_loss/atr_exit/atr_mult/
         avwap_stop_buf/cons_weak_exit/cons_weak_th/activity_decay_exit/
         activity_decay_days/max_hold/exit_enabled。
    用 kw 逐项覆盖后返回。默认值与 BT.default_params() 及 simulate 各 cfg.get 默认一致。
    """
    c = dict(BT.default_params())
    c.update({
        # ---- 平台适配：组合 / 撮合 / 出场 ----
        'initial_capital': 1_000_000,   # 初始资金
        'max_weight': 0.20,             # 单票封顶权重
        'allow_bear': False,            # 仅允许共振>=0 的多头
        'min_list_days': 60,            # 最小上市自然日
        'exclude_st': True,             # 过滤 ST
        'stop_loss': None,              # 固定止损（None 关闭）
        'atr_exit': False,              # 是否启用 ATR 止损
        'atr_mult': 3.0,                # ATR 止损倍数
        'avwap_stop_buf': 0.02,         # 结构/AVWAP 止损缓冲
        'cons_weak_exit': True,         # 共振走弱出场
        'cons_weak_th': -2,             # 共振走弱阈值
        'activity_decay_exit': False,   # 活跃度衰减出场
        'activity_decay_days': 3,       # 连续衰减天数
        'max_hold': None,               # 最长持有自然日
        'exit_enabled': True,           # 整体关闭出场
        # ---- 移动止盈 / 趋势破坏出场（解决“持有过久、高位回吐才被清”）----
        'trail_exit': False,            # 移动止盈/回撤出场
        'trail_pct': 0.10,              # 峰值回撤阈值
        'trail_min_gain': 0.03,         # 触发移动止盈所需最低浮盈
        'trend_exit': False,            # 趋势/AVWAP 破坏出场
        'trend_buf': 0.0,               # 跌破 AVWAP 缓冲
        # ---- 管道 B 入场：盘整/压缩前置 ----
        'use_compression_b_pre': False, # 是否要求管道B入场前有盘整
        'comp_b_pct_th': 0.12,          # 前期(约2周)波幅上限（ratcheting 压缩口径）
        # ---- 逐条条件开关（默认全开=与原先全 AND 一致；关闭则该条件不拦截）----
        'use_activity': True,           # 需要活跃度（两管道）
        'use_clean_level': True,        # 需要干净价位（两管道）
        'use_breakout': True,           # 需要突破箱顶（管道A）
        'use_compression': True,        # 需要盘整压缩（管道A）
        'use_roc': True,                # 需要突破速度 ROC（管道A）
        'use_emotion': True,            # 需要极端情绪（管道B）
        'use_reversal_entry': True,     # 需要反转入场确认（管道B）
        'use_resonance': True,          # 需要多头共振（多空偏置 gate）
    })
    c.update(kw)
    return c


def preset_cfgs():
    """Breitstein 具名预设：每种对应一种五要点玩法。"""
    return {
        # 默认活跃动量：活跃度 + 管道A（活跃突破）主导的全面型
        'active_momentum': base_cfg(),
        # 投降反弹：强调管道B（投降+收复AVWAP），放量更敏感、仓位更保守
        'capitulation_bounce': base_cfg(
            pipeline_b_emotion_th=2.0, capitulate_vol_mult=1.5, max_weight=0.15),
        # 窄幅盘整：压缩更严（更紧的区间/漂移门槛），活跃要求略降
        'narrow_consolidation': base_cfg(
            pct_th=0.05, drift_th=0.2, activity_th=1.6),
        # 强共振：仅多头共振入场，共振走弱即仓，拉长持有
        'strong_resonance': base_cfg(
            allow_bear=False, cons_weak_exit=True, max_hold=20),
    }


def bt_grid():
    """批量网格：活跃门槛 × 压缩阈值 × 情绪门槛 × 管道偏置 = 24 组。"""
    out = []
    for act, pct, emo, pipe in itertools.product(
            [1.3, 1.5, 1.8], [0.08, 0.12], [2.0, 3.0],
            ['breakout', 'reversal']):
        out.append(base_cfg(activity_th=act, pct_th=pct,
                            pipeline_b_emotion_th=emo, pipeline=pipe))
    return out


def entry_label(cfg):
    pipe = cfg.get('pipeline', 'breakout')
    tags = {'breakout': '管道A突破', 'reversal': '管道B反转', 'both': '管道A+B'}
    tag = tags.get(pipe, pipe)
    act = float(cfg.get('activity_th', 1.5))
    emo = float(cfg.get('pipeline_b_emotion_th', 2.0))
    return '%s|active>=%g|emotion>=%g' % (tag, act, emo)


def exit_label(cfg):
    if not cfg.get('exit_enabled', True):
        return '出场关闭'
    parts = []
    if cfg.get('stop_loss') is not None:
        parts.append('固定止损%d%%' % round(float(cfg['stop_loss']) * 100))
    if cfg.get('atr_exit'):
        parts.append('ATRx%g' % float(cfg.get('atr_mult', 3.0)))
    parts.append('结构AVWAP止损')
    if cfg.get('cons_weak_exit'):
        parts.append('共振走弱')
    if cfg.get('activity_decay_exit'):
        parts.append('活跃衰减')
    if cfg.get('max_hold') is not None:
        parts.append('持有%d日' % int(cfg['max_hold']))
    return '+'.join(parts) if parts else '仅到期'


# ==================== 层6：评估与输出 ====================

def equity_stats(eq_df):
    if eq_df is None or not len(eq_df):
        return dict(final=0.0, peak=0.0, mdd=0.0)
    eq = eq_df['equity'].values
    final = (eq[-1] - 1.0) * 100.0
    peak = (np.max(eq) - 1.0) * 100.0
    mdd = float(np.min(eq / np.maximum.accumulate(eq) - 1.0)) * 100.0
    return dict(final=final, peak=peak, mdd=mdd)


def trade_stats(tr):
    """批次明细统计：ret 已为扣费净收益；med_hold 为自然日中位。"""
    if tr is None or not len(tr):
        return dict(n_trades=0, n_open=0, win_rate=np.nan, pf=np.nan,
                    med_hold=np.nan)
    closed = tr[tr['exit_reason'] != 'open']
    n_open = int(len(tr) - len(closed))
    if not len(closed):
        return dict(n_trades=0, n_open=n_open, win_rate=np.nan, pf=np.nan,
                    med_hold=np.nan)
    r = closed['ret']
    w, l = r > 0, r <= 0
    denom = abs(float(r[l].sum()))
    pf = (float(r[w].sum()) / denom) if denom > 0 else np.nan
    hold = (pd.to_datetime(closed['exit_date'])
            - pd.to_datetime(closed['entry_date'])).dt.days
    return dict(n_trades=len(closed), n_open=n_open,
                win_rate=float(w.mean()) * 100.0, pf=float(pf),
                med_hold=float(hold.median()))


def evaluate(cfg, ncal, panels, mask_cache, tr_s, tr_e, oo_s, oo_e, fee_bps):
    """跑全区间 + train + oos 三段，汇总净值/批次指标；返回行字典与明细。"""
    full_tr, full_eq, info = simulate(
        ncal, panels, cfg, ncal[0], ncal[-1], mask_cache,
        collect_equity=True, fee_bps=fee_bps)
    tr_tr, tr_eq, _ = simulate(
        ncal, panels, cfg, tr_s, tr_e, mask_cache,
        collect_equity=True, fee_bps=fee_bps)
    oo_tr, oo_eq, _ = simulate(
        ncal, panels, cfg, oo_s, oo_e, mask_cache,
        collect_equity=True, fee_bps=fee_bps)

    fs, os_ = equity_stats(full_eq), equity_stats(oo_eq)
    ts_ = trade_stats(full_tr)
    tr_final = equity_stats(tr_eq)['final']

    flag = ''
    if len(oo_tr) < 30:
        flag = '小样本'
    if tr_final * os_['final'] < 0:
        flag = (flag + ';' if flag else '') + 'train/oos不稳'

    row = dict(pipeline=cfg.get('pipeline', 'breakout'),
               activity_th=round(float(cfg.get('activity_th', 1.5)), 2),
               pct_th=round(float(cfg.get('pct_th', 0.10)), 3),
               emotion_th=round(float(cfg.get('pipeline_b_emotion_th', 2.0)), 2),
               max_weight=round(float(cfg.get('max_weight', 0.20)), 2),
               entry=entry_label(cfg), exit=exit_label(cfg),
               final=round(fs['final'], 2), peak=round(fs['peak'], 2),
               mdd=round(fs['mdd'], 2),
               train_final=round(tr_final, 2),
               oos_final=round(os_['final'], 2), oos_mdd=round(os_['mdd'], 2),
               oos_n=len(oo_tr),
               n_trades=ts_['n_trades'],
               win_rate=round(ts_['win_rate'], 1) if ts_['n_trades'] else np.nan,
               pf=round(ts_['pf'], 2) if ts_['n_trades'] else np.nan,
               med_hold=ts_['med_hold'] if ts_['n_trades'] else np.nan,
               n_entries=info['n_entries'], flag=flag)
    detail = {'trades': full_tr, 'equity': full_eq, 'cfg': cfg}
    return row, detail


def _print_table(df, title):
    cols = ['pipeline', 'activity_th', 'pct_th', 'emotion_th', 'max_weight',
            'final', 'peak', 'mdd', 'train_final', 'oos_final', 'oos_mdd',
            'oos_n', 'n_trades', 'win_rate', 'pf', 'med_hold', 'flag']
    cols = [c for c in cols if c in df.columns]
    print('\n===== %s =====' % title)
    print(df[cols].to_string(index=False, float_format=lambda x: '%.2f' % x))


def _save_detail(cid, detail, tag):
    tr, eq = detail['trades'], detail['equity']
    ft = os.path.join(OUT_DIR, 'factor_lab_trades_%s_%s.csv' % (tag, cid))
    fe = os.path.join(OUT_DIR, 'factor_lab_equity_%s_%s.csv' % (tag, cid))
    tr.to_csv(ft, index=False, encoding='utf-8-sig')
    eq.to_csv(fe, index=False, encoding='utf-8-sig')
    return ft, fe


# ==================== 层7：扩展指标 / 基准 / 编程接口 ====================

TRADING_DAYS = 252


def make_tag(user_id, start, end):
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    return 'u%s_%s_%s' % (str(user_id or 'mkt'),
                          start.strftime('%m%d'), end.strftime('%m%d'))


def extended_stats(eq_df, bench_ret=None, rf=0.0):
    """在 equity_stats 基础上扩展年化/波动/Sharpe/Calmar/Alpha/Beta/IR。

    eq_df 需含 date, port_ret, equity；bench_ret 为对齐后同索引的基准日收益序列。
    既有 equity_stats(final/peak/mdd) 字段含义不变。
    """
    out = equity_stats(eq_df)
    out.update(ann_return=np.nan, ann_vol=np.nan, sharpe=np.nan, calmar=np.nan,
               alpha=np.nan, beta=np.nan, info_ratio=np.nan, bench_final=np.nan)
    if eq_df is None or not len(eq_df):
        return out
    r = eq_df['port_ret'].values
    n = len(r)
    if n >= 2:
        ann_return = (eq_df['equity'].iloc[-1]) ** (TRADING_DAYS / n) - 1.0
        ann_vol = float(np.std(r, ddof=1)) * np.sqrt(TRADING_DAYS)
        sharpe = (ann_return - rf) / ann_vol if ann_vol > 0 else np.nan
        mdd_abs = abs(out['mdd']) / 100.0
        calmar = ann_return / mdd_abs if mdd_abs > 0 else np.nan
        out.update(ann_return=ann_return * 100.0, ann_vol=ann_vol * 100.0,
                   sharpe=float(sharpe), calmar=float(calmar))
    if bench_ret is not None and len(bench_ret) >= 2:
        br = np.asarray(bench_ret, dtype=float)
        cov = float(np.cov(r, br, ddof=1)[0, 1])
        var_b = float(np.var(br, ddof=1))
        beta = cov / var_b if var_b > 0 else np.nan
        excess = r - br
        ir_vol = float(np.std(excess, ddof=1)) * np.sqrt(TRADING_DAYS)
        ann_excess = float(np.mean(excess)) * TRADING_DAYS
        info_ratio = ann_excess / ir_vol if ir_vol > 0 else np.nan
        alpha = (float(np.mean(excess)) * TRADING_DAYS) * 100.0
        out.update(alpha=alpha, beta=float(beta), info_ratio=float(info_ratio),
                   bench_final=((1.0 + br).prod() - 1.0) * 100.0)
    return out


_BENCH_MEMO = {}
BENCH_CACHE_VERSION = 1


def _bench_cache_path(bench_code):
    code = str(PB.to_code(bench_code or DEFAULT_BENCHMARK))
    safe = ''.join(ch if (ch.isalnum() or ch == '_') else '_' for ch in code)
    return os.path.join(platform_env.CACHE_DIR,
                        '_factorlab_bench_%s.pkl' % safe)


def _load_bench_disk(bench_code):
    blob = cache_crypto.load_blob(_bench_cache_path(bench_code))
    if not blob or blob.get('version') != BENCH_CACHE_VERSION:
        return None
    ser = blob.get('close')
    if ser is None or not len(ser):
        return None
    ser = pd.Series(ser)
    ser.index = pd.to_datetime(ser.index)
    return ser.sort_index()


def _save_bench_disk(bench_code, ser):
    if ser is None or not len(ser):
        return
    cache_crypto.save_blob(
        _bench_cache_path(bench_code),
        {'version': BENCH_CACHE_VERSION, 'code': str(
            PB.to_code(bench_code or DEFAULT_BENCHMARK)), 'close': ser})


def get_benchmark_with_fallback(provider, bench_code, start, end):
    """基准取数：在线优先、成功落盘；数据源失败时离线复用本地缓存。

    返回 (close Series, degraded)。degraded 为 None 表示在线数据；
    为提示字符串表示已降级到本地缓存（区间可能不全/非最新）；
    在线失败且本地无缓存时抛出最后一个数据源异常。
    """
    key = (provider.name, bench_code, start, end)
    if key in _BENCH_MEMO:
        return _BENCH_MEMO[key]
    try:
        ser = provider.benchmark(bench_code, start, end)
        if ser is not None and len(ser):
            _save_bench_disk(bench_code, ser)
            result = (ser, None)
        else:
            raise RuntimeError('基准接口返回空数据')
    except Exception as src_err:
        cached = _load_bench_disk(bench_code)
        if cached is None:
            raise src_err
        result = (cached, '基准数据源不可用（%s），已改用本地缓存基准，'
                          '区间或时点可能不完整。' % src_err)
    _BENCH_MEMO[key] = result
    return result


def get_benchmark(provider, bench_code, start, end):
    """基准收盘价去重入口（兼容旧调用，仅返回 Series）。"""
    return get_benchmark_with_fallback(provider, bench_code, start, end)[0]


def align_benchmark(eq_df, bench_close):
    """基准收盘按 eq_df 交易日对齐，返回 (归一净值序列, 日收益序列)；缺基准返回 (None,None)。"""
    if bench_close is None or not len(bench_close) or eq_df is None or not len(eq_df):
        return None, None
    s = pd.Series(bench_close).copy()
    s.index = pd.to_datetime(s.index)
    dates = pd.to_datetime(eq_df['date']).values
    aligned = s.reindex(s.index.union(dates)).ffill().reindex(dates)
    aligned = pd.to_numeric(aligned, errors='coerce')
    if aligned.isna().any() or len(aligned) < 2:
        return None, None
    nav = aligned / aligned.iloc[0]
    return nav.values, nav.pct_change().fillna(0.0).values


def yearly_returns(eq_df):
    """逐年策略收益（按年内日收益复利）。返回 DataFrame[year, ret%]。"""
    if eq_df is None or not len(eq_df):
        return pd.DataFrame(columns=['year', 'ret'])
    d = eq_df.copy()
    d['year'] = pd.to_datetime(d['date']).dt.year
    rows = [{'year': y, 'ret': round(((1.0 + g['port_ret']).prod() - 1.0) * 100.0, 2)}
            for y, g in d.groupby('year')]
    return pd.DataFrame(rows)


def monthly_returns(eq_df):
    """月度收益透视（年行 x 12 月列），用于热力图。"""
    if eq_df is None or not len(eq_df):
        return pd.DataFrame()
    d = eq_df.copy()
    d['ym'] = pd.to_datetime(d['date']).dt.to_period('M')
    m = d.groupby('ym')['port_ret'].apply(lambda x: (1.0 + x).prod() - 1.0)
    p = pd.DataFrame({'year': m.index.year, 'month': m.index.month, 'v': m.values})
    return p.pivot_table(index='year', columns='month', values='v').reindex(
        columns=range(1, 13))


def backtest_once(cfg, start, end, user_id=None, fee_bps=0.0, nproc=None,
                  provider=None, panels_state=None, bench_code=None, rf=0.0):
    """编程接口：一次回测的完整编排，供 UI / 批量复用，CLI 与 UI 共用同一实现。

    panels_state: 可选 (cal, panels, tag)，提供则跳过重载（UI 缓存）；否则经
                  DataProvider 取数并构建（走 load_or_build 缓存）。
    返回 dict：cfg/tag/cal/panels/trades/equity/bench_nav/metrics/yearly/monthly/info。
    """
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if provider is None:
        from data_provider import MysqlProvider
        provider = MysqlProvider()
    if nproc is None:
        nproc = PB.resolve_nproc()
    tag = make_tag(user_id, start, end)
    if panels_state is not None:
        cal, panels = panels_state[0], panels_state[1]
    else:
        cal, panels, _degraded = load_or_build(tag, user_id, start, end, nproc)

    trades, eq_df, info = simulate(
        cal, panels, cfg, start, end, {}, collect_equity=True, fee_bps=fee_bps)

    bench_code = bench_code or DEFAULT_BENCHMARK
    bench_degraded = None
    try:
        bench_close, bench_degraded = get_benchmark_with_fallback(
            provider, bench_code, start, end)
    except Exception:
        bench_close = pd.Series(dtype=float)
    bench_nav, bench_ret = align_benchmark(eq_df, bench_close)
    metrics = extended_stats(eq_df, bench_ret, rf=rf)
    metrics.update(trade_stats(trades))
    metrics['n_entries'] = info['n_entries']

    if bench_nav is not None and eq_df is not None and len(eq_df):
        eq_df = eq_df.copy()
        eq_df['bench_equity'] = bench_nav

    return {'cfg': cfg, 'tag': tag, 'cal': cal, 'panels': panels,
            'trades': trades, 'equity': eq_df, 'bench_nav': bench_nav,
            'bench_degraded': bench_degraded,
            'metrics': metrics, 'info': info,
            'yearly': yearly_returns(eq_df), 'monthly': monthly_returns(eq_df)}


# ==================== 单日选股：Breitstein 快捷口径 ====================

# 快捷口径：每种是对"date 当日某只股票"是否命中某条件的过滤（不依赖 p['event']）。
PICK_MODES = {
    'active':    '活跃TopN',       # 横截面按 activity_raw 当日值取前 topn
    'breakout':  '盘整突破',       # 管道A命中（活跃+箱顶突破+前日压缩+干净阻力）
    'reversal':  '投降+收复AVWAP', # 管道B命中（entry_reversal+情绪极值）
    'resonance': '共振机会',       # 多头共振（resonance>=2 且活跃）
}


def _num(val):
    try:
        v = float(val)
        return round(v, 4) if np.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _panel_fval(p, key, i):
    """面板数组取连续值：缺失/越界/非有限返回 None（于 module 顶层可用）。"""
    a = p.get(key)
    if a is None or i is None or i < 0 or i >= len(a):
        return None
    return _num(a[i])


def _active(p, i, cfg):
    """运行时活跃判定（回测门控 + 单日选股共用的“三腿 OR”语义）。

    思路：把原先只生成无人消费布尔列的构建期死参数
    atr_ratio_th/vol_ratio_th/gap_th 改为运行时阈值——面板已持久化
    atr_ratio/volume_ratio/gap_pct 连续值，可直接逐日对照。

    判定 = activity_raw ≥ activity_th（总入口门槛）
          AND (atr_ratio > atr_ratio_th  OR  volume_ratio > vol_ratio_th
               OR  abs(gap_pct) > gap_th)
    即“任一腿异常放大”即视为活跃。改左侧滑块 + 点运行即实时改变选中结果，
    不再需要重建面板。cfg 缺省用 base_cfg() 的默认值。
    """
    cfg = base_cfg() if cfg is None else dict(cfg)
    act = _panel_fval(p, 'activity_raw', i)
    if act is None or act < float(cfg.get('activity_th', 1.5)):
        return False
    atr = _panel_fval(p, 'atr_ratio', i)
    vol = _panel_fval(p, 'volume_ratio', i)
    gap = _panel_fval(p, 'gap_pct', i)
    leg = (atr is not None and atr > float(cfg.get('atr_ratio_th', 2.0))) \
        or (vol is not None and vol > float(cfg.get('vol_ratio_th', 1.8))) \
        or (gap is not None and abs(gap) > float(cfg.get('gap_th', 0.02)))
    return leg


def picks_for_date(cal, panels, date, mode='reversal', require_major=False, cfg=None):
    """还原 date 当日收盘的 Breitstein 快捷选股名单（不依赖账户模拟）。

    四种口径（PICK_MODES）对"date 当日某只股票"做条件过滤，返回各标的的行：
      opportunity/rxcode/code/name/close/activity_score(=activity_raw)/clean_level/
      level_conf/range_pct/range_z/emotion_score/resonance/position_mult/tradable。
      tradable = 非ST、非次新、未封涨停；require_major=True 时另要求 resonance>0。
    active 口径按 activity_raw 降序取前 topn；其余按 (resonance, activity_raw) 降序。
    cfg 为用于命中的参数（activity_th/conf_th/pipeline_b_emotion_th/pct_th/z_th/
    roc_min/comp_window/topn），缺省用 base_cfg() 的默认值，便于前端把侧边栏参数传入。
    返回 (DataFrame, {'n': 数量})；无数据返回空表 + {'n': 0}。
    """
    date = pd.Timestamp(date)
    cal_dates = set(pd.Timestamp(x) for x in cal)
    if date not in cal_dates:
        raise ValueError('%s 不在面板交易日历（库内最新 %s）'
                         % (date.date(), pd.Timestamp(cal[-1]).date()))
    if mode not in PICK_MODES:
        raise ValueError('未知单日选股口径 %s（可选 %s）'
                         % (mode, sorted(PICK_MODES)))
    cfg = base_cfg() if cfg is None else dict(cfg)
    conf_th = float(cfg['conf_th'])
    emotion_th = float(cfg['pipeline_b_emotion_th'])
    pct_th = float(cfg['pct_th'])
    z_th = float(cfg['z_th'])
    roc_min = float(cfg['roc_min'])
    box_n = int(cfg['comp_window'])
    min_list_days = int(cfg['min_list_days'])
    topn = int(cfg['topn'])
    label = PICK_MODES[mode]

    rows = []
    for rx, p in panels.items():
        i = p['pos'].get(date)
        if i is None:
            continue
        act = _num(p['activity_raw'][i]) if 'activity_raw' in p else None

        # ---- per-mode 命中判定 ----
        hit = False
        if mode == 'active':
            # 活跃达标即可进入排序池；后续按 activity_raw 取前 topn
            hit = _active(p, i, cfg)
        elif mode == 'breakout':
            if _active(p, i, cfg):
                hi = p.get('high')
                bx = None
                if hi is not None:
                    bx = pd.Series(hi, dtype='float64').rolling(
                        box_n).max().shift(1).values
                cp = _num(p['close'][i])
                bh = (bx[i] if bx is not None and i < len(bx) else None)
                rp1 = _num(p['range_pct'][i - 1]) if i - 1 >= 0 else None
                rz1 = _num(p['range_z'][i - 1]) if i - 1 >= 0 else None
                roc = _num(p['breakout_roc'][i]) if 'breakout_roc' in p else None
                cfr = _num(p['level_conf_r'][i]) if 'level_conf_r' in p else None
                comp = ((rp1 is not None and rp1 < pct_th)
                        or (rz1 is not None and rz1 < z_th))
                if bh and cp is not None and cp > bh and comp \
                        and roc is not None and roc > roc_min \
                        and cfr is not None and cfr > conf_th:
                    hit = True
        elif mode == 'reversal':
            er = bool(p['entry_reversal'][i]) if 'entry_reversal' in p else False
            emo = _num(p['emotion_score'][i]) if 'emotion_score' in p else None
            cfs = _num(p['level_conf_s'][i]) if 'level_conf_s' in p else None
            if (er and _active(p, i, cfg)
                    and cfs is not None and cfs > conf_th
                    and emo is not None and abs(emo) >= emotion_th):
                hit = True
        elif mode == 'resonance':
            res = _num(p['resonance'][i]) if 'resonance' in p else None
            if res is not None and res >= 2 and _active(p, i, cfg):
                hit = True

        if not hit:
            continue

        # ---- 行字段 ----
        if mode == 'breakout':
            clean_level = (_num(p['level_resistance'][i])
                           if 'level_resistance' in p else None)
            level_conf = (_num(p['level_conf_r'][i])
                          if 'level_conf_r' in p else None)
        elif mode == 'reversal':
            clean_level = (_num(p['level_support'][i])
                           if 'level_support' in p else None)
            level_conf = (_num(p['level_conf_s'][i])
                          if 'level_conf_s' in p else None)
        else:
            clean_level = None
            level_conf = (_num(p['level_conf_r'][i])
                          if 'level_conf_r' in p else None)

        res = _num(p['resonance'][i]) if 'resonance' in p else None
        row = {
            'opportunity': label,
            'rxcode': rx,
            'code': p.get('code', ''),
            'name': p.get('name', ''),
            'close': _num(p['close'][i]),
            'activity_score': act,
            'clean_level': clean_level,
            'level_conf': level_conf,
            'range_pct': _num(p['range_pct'][i]) if 'range_pct' in p else None,
            'range_z': _num(p['range_z'][i]) if 'range_z' in p else None,
            'emotion_score': _num(p['emotion_score'][i])
            if 'emotion_score' in p else None,
            'resonance': res,
            'position_mult': _num(p['position_mult'][i])
            if 'position_mult' in p else None,
        }

        # ---- tradable：非ST、非次新、未封涨停（与 simulate 的 A股约束对齐）----
        is_st = p.get('is_st')
        blocked = bool(is_st is not None and i < len(is_st) and int(is_st[i]) == 1)
        ld = p.get('list_date')
        if ld is not None and not pd.isna(ld) \
                and (date - pd.Timestamp(ld)).days < min_list_days:
            blocked = True
        lu = _num(p['limit_up'][i]) if 'limit_up' in p else None
        cp = row['close']
        if lu and cp is not None and cp >= lu * 0.999:
            blocked = True
        if require_major and (res is None or res <= 0):
            blocked = True
        row['tradable'] = not blocked

        rows.append(row)

    cols = ['opportunity', 'rxcode', 'code', 'name', 'close',
            'activity_score', 'clean_level', 'level_conf', 'range_pct',
            'range_z', 'emotion_score', 'resonance', 'position_mult',
            'tradable']
    if not rows:
        return pd.DataFrame(columns=['rank_no'] + cols), {'n': 0}
    d = pd.DataFrame(rows)
    if mode == 'active':
        d = d.sort_values(['activity_score', 'rxcode'],
                          ascending=[False, True]).reset_index(drop=True)[:topn]
    else:
        d = d.sort_values(['resonance', 'activity_score', 'rxcode'],
                          ascending=[False, False, True]).reset_index(drop=True)
    d.insert(0, 'rank_no', range(1, len(d) + 1))
    return d.reindex(columns=['rank_no'] + cols), {'n': len(d)}


def run_picks(args, start, end, cal, panels):
    date = pd.Timestamp(args.date)
    picked, meta = picks_for_date(cal, panels, date, args.pick_mode,
                                  require_major=bool(args.require_major))
    print('单日选股 %s（口径=%s）panels=%d 当日事件=%d'
          % (date.date(), args.pick_mode, len(panels), meta['n']))
    if len(picked):
        print(picked.to_string(index=False))
    else:
        print('当日无符合条件的标的')
    fp = os.path.join(OUT_DIR, 'factor_lab_picks_%s_%s.csv'
                      % (date.strftime('%Y%m%d'), args.pick_mode))
    picked.to_csv(fp, index=False, encoding='utf-8-sig')
    print('已保存:', fp)


def cal_idx_of(cal, date):
    date = pd.Timestamp(date)
    for i, x in enumerate(cal):
        if pd.Timestamp(x) == date:
            return i
    return None


# ==================== 自定义模式：命令行 Breitstein 参数 ====================

def _pct_or_none(v):
    return (v / 100.0) if v and v > 0 else None


def custom_cfg(args):
    """把 custom 命令行参数组装成有效 Breitstein cfg；未指定项取默认。

    Breitstein 指标阈值统一由 base_cfg() 填充；此处仅透传仍有效的平台/出场键。
    """
    return base_cfg(
        topn=args.topn,
        stop_loss=_pct_or_none(args.stop_loss_pct),
        atr_exit=bool(args.atr_stop), atr_mult=args.atr_mult,
        max_hold=(args.max_hold_days if args.max_hold_days > 0 else None))


def run_custom(args, start, end, cal, panels):
    """自定义模式：Breitstein 参数自由组合，单组全区间回测并打印扩展指标。"""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    cfg = custom_cfg(args)
    print('自定义模式 %s..%s panels=%d 费%s bps'
          % (start.date(), end.date(), len(panels), args.fee_bps))
    print('建仓: %s' % entry_label(cfg))
    print('出场: %s' % exit_label(cfg))

    trades, eq_df, info = simulate(
        cal, panels, cfg, start, end, {}, collect_equity=True,
        fee_bps=args.fee_bps)

    bench_ret = None
    try:
        from data_provider import get_provider
        provider = get_provider(args.source)
        bench_close = get_benchmark(provider, DEFAULT_BENCHMARK, start, end)
        _, bench_ret = align_benchmark(eq_df, bench_close)
    except Exception as e:
        print('基准获取失败，仅输出无基准指标:', e)

    m = extended_stats(eq_df, bench_ret)
    m.update(trade_stats(trades))
    print('\n===== custom 结果 =====')
    print('总收益 %.2f%%  年化 %.2f%%  年化波动 %.2f%%  最大回撤 %.2f%%'
          % (m['final'], m['ann_return'], m['ann_vol'], m['mdd']))
    print('Sharpe %.2f  Calmar %.2f  Alpha %.2f%%  Beta %.2f  信息比率 %.2f  基准 %.2f%%'
          % (m['sharpe'], m['calmar'], m['alpha'], m['beta'],
             m['info_ratio'], m['bench_final']))
    print('批次 %d 笔  胜率 %.1f%%  盈亏比 %.2f  建仓计划 %d'
          % (m['n_trades'], m['win_rate'], m['pf'], info['n_entries']))

    detail = {'trades': trades, 'equity': eq_df, 'cfg': cfg}
    ft, fe = _save_detail('custom', detail, make_tag(
        None if args.user_id == 0 else args.user_id, start, end))
    print('已保存: %s | %s' % (ft, fe))


def run():
    ap = argparse.ArgumentParser()
    ap.add_argument('--user-id', type=int, default=0,
                    help='自选股用户 ID，默认 0 表示全市场')
    ap.add_argument('--start', default='2026-03-01')
    ap.add_argument('--end', default='2026-09-18')
    ap.add_argument('--split', type=float, default=0.65)
    ap.add_argument('--stage', choices=['grid', 'presets', 'both', 'picks',
                                        'custom'],
                    default='both')
    ap.add_argument('--date', default=None, help='picks：查询交易日 YYYY-MM-DD')
    ap.add_argument('--pick-mode', choices=sorted(PICK_MODES), default='reversal',
                    help='picks 快捷口径：active/breakout/reversal/resonance')
    ap.add_argument('--require-major', action='store_true',
                    help='picks 名单要求 resonance>0 才可交易')
    ap.add_argument('--top-k', type=int, default=4)
    ap.add_argument('--fee-bps', type=float, default=0.0,
                    help='单边费率 bp；万3=3')
    ap.add_argument('--procs', type=int, default=PB.resolve_nproc())
    ap.add_argument('--rebuild-cache', action='store_true')
    ap.add_argument('--plot', action='store_true')

    # custom：数据源
    ap.add_argument('--source', choices=['mysql', 'api', 'import'],
                    default='mysql')
    # custom：建仓
    ap.add_argument('--entry-path', choices=['lps_sos', 'sos_bu'],
                    default='lps_sos')
    ap.add_argument('--first-weight', type=float, default=50.0,
                    help='首批比例百分数，默认 50')
    ap.add_argument('--spring-entry', action='store_true')
    ap.add_argument('--no-major-gate', action='store_true')
    ap.add_argument('--full-breakout', action='store_true')
    ap.add_argument('--topn', type=int, default=5)
    # custom：出场
    ap.add_argument('--stop-loss-pct', dest='stop_loss_pct', type=float,
                    default=0.0)
    ap.add_argument('--atr-stop', action='store_true')
    ap.add_argument('--atr-mult', type=float, default=3.0)
    ap.add_argument('--pf-exit', action='store_true')
    ap.add_argument('--bear-exit', type=float, default=4)
    ap.add_argument('--max-hold-days', type=int, default=0)
    # custom：guard
    ap.add_argument('--guard', action='store_true')
    ap.add_argument('--guard-dd-pct', dest='guard_dd_pct', type=float,
                    default=8.0)
    ap.add_argument('--guard-lookback', dest='guard_lookback', type=int,
                    default=20)
    args = ap.parse_args()

    user_id = None if args.user_id == 0 else args.user_id
    start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
    if args.stage == 'picks':
        if not args.date:
            raise SystemExit('picks 阶段必须指定 --date YYYY-MM-DD')
        end = max(end, pd.Timestamp(args.date))
    tag = make_tag(user_id, start, end)

    cal, panels, _degraded = load_or_build(tag, user_id, start, end, args.procs,
                                           rebuild=args.rebuild_cache)
    if args.stage == 'picks':
        run_picks(args, start, end, cal, panels)
        return
    if args.stage == 'custom':
        run_custom(args, start, end, cal, panels)
        return
    ncal = [pd.Timestamp(x) for x in cal if start <= pd.Timestamp(x) <= end]
    cut_i = int(len(ncal) * args.split)
    tr_s, tr_e = ncal[0], ncal[cut_i - 1]
    oo_s, oo_e = ncal[cut_i], ncal[-1]
    print('信号区间 train=%s..%s(%d日) oos=%s..%s(%d日) panels=%d 口径: fav%s '
          '信号日收盘价 槽位×批次日复利 费%s bps'
          % (tr_s.date(), tr_e.date(), cut_i, oo_s.date(), oo_e.date(),
             len(ncal) - cut_i, len(panels), user_id, args.fee_bps))

    mask_cache = {}
    saved = {}

    # ---------- presets ----------
    if args.stage in ('presets', 'both'):
        rows, details = [], {}
        for cid, cfg in preset_cfgs().items():
            row, det = evaluate(cfg, ncal, panels, mask_cache,
                                tr_s, tr_e, oo_s, oo_e, args.fee_bps)
            row['cid'] = cid
            rows.append(row)
            details[cid] = det
        dfp = pd.DataFrame(rows).sort_values(
            ['oos_final', 'oos_mdd'], ascending=[False, True]).reset_index(drop=True)
        _print_table(dfp, 'presets Breitstein 预设（全区间 / OOS 净值%）')
        for cid, det in details.items():
            _save_detail(cid, det, tag)
            saved[cid] = det

    # ---------- grid ----------
    if args.stage in ('grid', 'both'):
        rows = []
        t0 = time.time()
        for k, cfg in enumerate(bt_grid(), 1):
            row, _ = evaluate(cfg, ncal, panels, mask_cache,
                              tr_s, tr_e, oo_s, oo_e, args.fee_bps)
            row['cid'] = 'grid_%02d' % k
            rows.append(row)
        dfg = pd.DataFrame(rows).sort_values(
            ['oos_final', 'oos_mdd'], ascending=[False, True]).reset_index(drop=True)
        fg = os.path.join(OUT_DIR, 'factor_lab_grid_%s.csv' % tag)
        dfg.to_csv(fg, index=False, encoding='utf-8-sig')
        _print_table(dfg, '网格 活跃×压缩×情绪×管道偏置 共%d 组（按 OOS 降序）'
                     % len(dfg))
        print('网格已保存:', fg, ' 用时%.0fs' % (time.time() - t0))
        if args.plot:
            try:
                _plot(dfg, tag)
            except Exception as e:
                print('绘图失败（不影响主流程）:', e)


def _plot(dfg, tag):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.axhline(1.0, color='black', lw=0.6)
    ax.set_title('factor_lab grid (%s)' % tag)
    ax.legend()
    ax.grid(alpha=0.3)
    fp = os.path.join(OUT_DIR, 'factor_lab_equity_%s_top.png' % tag)
    fig.tight_layout()
    fig.savefig(fp, dpi=110)
    plt.close(fig)
    print('净值图:', fp)


if __name__ == '__main__':
    import multiprocessing as mp
    mp.freeze_support()
    run()
