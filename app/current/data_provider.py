# coding: utf-8
"""
data_provider —— rxlib 统一数据提供层。

上层（面板 / 撮合 / 界面）只认 DataProvider 抽象，不写死数据库连接：
  MysqlProvider：经 quant 内自包含模块 rx_core 走只读 MySQL 链路；
  ApiProvider  ：HTTP 取数，返回同构 universe / kline，上层零改动，仅换实例。

数据契约以 docs/api_spec.md 为准：
  load_universe(user_id) -> (universe_df, st_set)
      universe_df 字段：code（6 位）, name, list_date, is_st
      user_id=None 表示全市场；给定值表示该用户自选股池。
  load_kline(codes, start, end) -> kline_df
      字段：code（6 位）, name, trade_date, open/high/low/close, volume,
            limit_up, is_st；区间自动含 start 之前约 2 个月（>=60 自然日）预热。
  benchmark(code, start, end) -> Series(index=trade_date, 值=收盘)
      默认沪深300（000300），从同一库月表取。
  latest_trade_date() -> date
      返回行情库内最新交易日。
  intraday_snapshot(date=None, force=False) -> (df, trade_date, tick_time)
      当日盘中快照；date=None 取今天。无数据时 df 为空、后两项为 None。
  intraday_latest() -> date | None
      数据源最新可用的盘中快照交易日；不可用时返回 None。

约束：全程只读，不写任何数据库表；连接用完即关。
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

import rx_core as PB

try:
    import sql_market as SQLM
except ImportError:
    class _MissingSQLM(object):
        def __getattr__(self, name):
            raise RuntimeError(
                '缺少本地直连行情 SQL 模块 sql_market.py，无法使用直连 MySQL 模式；'
                '请改用 API 模式，或参照 sql_market.example.py 放置本地 sql_market.py')

    SQLM = _MissingSQLM()

import sql_system as SQLS

DEFAULT_BENCHMARK = '000300'
WARMUP_DAYS = 60
_TZ8 = timezone(timedelta(hours=8))

# 模块级 ETag 条件缓存：{base_url: {cache_key: {'etag','data'}}}。
# 前端每次查询可能新建 ApiProvider 实例，缓存放模块级才能跨点击保留，
# 使重复查询真正命中 304，而不是每次都重建实例、丢失 ETag 后回 200。
_ETAG_STORE = {}

_CLIENT_ID_FILE = os.path.join('.rxlib', 'client_id')


def _client_id_path():
    return os.path.join(os.path.expanduser('~'), _CLIENT_ID_FILE)


def get_client_id():
    """本机客户端实例 UUID：持久化到 ~/.rxlib/client_id，跨进程稳定。

    首次调用生成并落盘；读取到合法 UUID 则复用。文件不可写等异常
    不阻断取数，退化为进程内随机 ID（仅本次运行有效）。
    """
    path = _client_id_path()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            value = f.read().strip()
        if value:
            uuid.UUID(value)
            return value
    except (OSError, ValueError):
        pass
    value = str(uuid.uuid4())
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(value)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass
    return value


def get_client_version():
    """客户端版本标识，随 X-Rxlib-Version 上报；取不到时降级 unknown。"""
    try:
        import platform_env
        return 'rxlib/%s' % getattr(platform_env, 'APP_VERSION', 'unknown')
    except Exception:
        return 'rxlib/unknown'


def to_bench_full(code):
    c = PB.to_code(code)
    if not c.isdigit():
        return code
    if c.startswith('399'):
        return c + '.SZ'
    if c.startswith(('000', '950')):
        return c + '.SH'
    return PB.to_full(c)


class ApiAuthError(RuntimeError):
    """API 鉴权失败（HTTP 401/403）：Key 无效、被吊销或设备被停用。

    上层据此与普通网络错误区分：历史回测可静默降级到本地缓存，
    仅在真正需要拉取新数据时才提示用户更换 Key。
    """


class DataProvider(object):
    name = 'base'

    def load_universe(self, user_id):
        raise NotImplementedError

    def load_kline(self, codes, start, end):
        raise NotImplementedError

    def benchmark(self, code, start, end):
        raise NotImplementedError

    def latest_trade_date(self):
        raise NotImplementedError

    def intraday_snapshot(self, date=None, force=False):
        """盘中快照，返回 (df, trade_date, tick_time)；无数据时 df 为空。"""
        raise NotImplementedError

    def intraday_latest(self):
        """数据源最新可用盘中快照交易日；不可用时返回 None。"""
        raise NotImplementedError


class MysqlProvider(DataProvider):
    name = 'mysql'

    def __init__(self, read_config=None):
        self.read_config = PB.READ_CONFIG if read_config is None else read_config

    def load_universe(self, user_id):
        """全市场股票池；直连模式下忽略 user_id（自选股走本地 CSV）。"""
        rconn = PB.connect_replica_db(self.read_config)
        try:
            return PB.load_universe(rconn)
        finally:
            rconn.close()

    def intraday_snapshot(self, date=None, force=False):
        day = pd.Timestamp(date) if date is not None else pd.Timestamp(
            datetime.now(_TZ8).date())
        df, tick = PB.load_instant(self.read_config, day)
        if df.empty:
            return df, None, None
        return df, day.strftime('%Y-%m-%d'), tick

    def intraday_latest(self):
        return PB.latest_instant_date(self.read_config)

    def load_kline(self, codes, start, end):
        start = pd.Timestamp(start)
        end = pd.Timestamp(end)
        rconn = PB.connect_replica_db(self.read_config)
        try:
            month_tables = PB.discover_month_tables(rconn, start, end)
        finally:
            rconn.close()
        df = PB.load_kline(self.read_config, set(codes), month_tables)
        if 'limit_up' not in df.columns and 'limit_up_price' in df.columns:
            df['limit_up'] = df['limit_up_price']
        lo = start - pd.Timedelta(days=WARMUP_DAYS)
        df = df[(df['trade_date'] >= lo) & (df['trade_date'] <= end)]
        return df.sort_values(['rxcode', 'trade_date']).reset_index(drop=True)

    def benchmark(self, code, start, end):
        start = pd.Timestamp(start)
        end = pd.Timestamp(end)
        full = to_bench_full(code)
        rconn = PB.connect_replica_db(self.read_config)
        try:
            month_tables = PB.discover_month_tables(rconn, start, end)
            frames = []
            for tbl in month_tables:
                t = pd.read_sql(SQLM.benchmark_close(tbl), rconn,
                                params=(full,))
                if len(t):
                    t = t.rename(columns={'close_price': 'close'})
                    frames.append(t)
        finally:
            rconn.close()
        if not frames:
            return pd.Series(dtype=float)
        s = pd.concat(frames, ignore_index=True)
        s['trade_date'] = pd.to_datetime(s['trade_date'])
        s['close'] = pd.to_numeric(s['close'], errors='coerce')
        s = s.dropna().sort_values('trade_date')
        return s.set_index('trade_date')['close']

    def latest_trade_date(self):
        rconn = PB.connect_replica_db(self.read_config)
        try:
            cur = rconn.cursor()
            cur.execute(SQLS.show_tables())
            tables = [r[0] for r in cur.fetchall()]
            tabs = sorted(t for t in tables if t.startswith('stock_daily_'))
            for tbl in reversed(tabs):
                cur.execute(SQLM.max_trade_date(tbl))
                row = cur.fetchone()
                if row and row[0] is not None:
                    return pd.Timestamp(row[0]).date()
        finally:
            rconn.close()
        raise RuntimeError('未找到任何 stock_daily_ 月度行情表')


class ApiProvider(DataProvider):
    """HTTP 数据提供层；须返回与 MysqlProvider 同构数据，上层零改动。

    接口路径、参数、返回字段和错误码以 docs/api_spec.md 为准。

    通用约定
      base_url       ：服务版本根地址，如 https://host/api/v1
      token          ：请求头 Authorization: Bearer <token>
      日期入参/出参  ：YYYY-MM-DD（如 2026-09-18）；代码一律 6 位 code，不带后缀
      价格           ：JSON number（不复权为原始价）；volume 为 number
      返回包裹       ：{"code": 0, "msg": "ok", "data": ...}，code!=0 视为失败
      失败           ：HTTP 非 2xx 或 code!=0 抛异常，由上层提示

    最小端点：
      GET /universe
      GET /kline
      GET /benchmark
      GET /latest-trade-date
    """
    name = 'api'

    def __init__(self, base_url=None, token=None, timeout=None, verify_ssl=None):
        # 参数优先；缺省从 [api] 配置 / 环境变量读取（经 platform_env）。
        cfg = {}
        try:
            import platform_env
            cfg = platform_env.get_api_config()
        except Exception:
            cfg = {}
        self.base_url = base_url if base_url is not None else cfg.get('base_url', '')
        self.token = token if token is not None else cfg.get('token', '')
        self.timeout = timeout if timeout is not None else cfg.get('timeout', 10)
        self.verify_ssl = verify_ssl if verify_ssl is not None else cfg.get('verify_ssl', True)
        self.client_id = get_client_id()
        self.client_version = get_client_version()
        self._client = None

    @staticmethod
    def _cache_key(path, params):
        if not params:
            return path
        return path + '?' + '&'.join(
            '%s=%s' % (k, params[k]) for k in sorted(params))

    @property
    def _etag_cache(self):
        # 按 base_url 取模块级共享缓存，跨实例 / 跨点击保留 ETag。
        return _ETAG_STORE.setdefault(self.base_url, {})

    def _ensure_config(self):
        if not self.base_url:
            raise RuntimeError(
                'ApiProvider 缺少 base_url：请在 config.ini [api] 配置 base_url，'
                '或设置环境变量 RXLIB_API_BASE_URL')
        if not self.token:
            raise RuntimeError(
                'ApiProvider 缺少 token：请在 config.ini [api] 配置 token，'
                '或设置环境变量 RXLIB_API_TOKEN')

    def _get_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.Client(timeout=self.timeout,
                                        verify=self.verify_ssl)
        return self._client

    def meta(self):
        return self._get('/meta')

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None

    def _get(self, path, params=None, timeout=None, force=False):
        """统一 GET：Bearer 鉴权，区分 HTTP / 业务码 / 超时三类错误。

        token 不写入异常信息与日志。timeout 可覆盖本次请求超时（秒）。
        force=True 时不发送 If-None-Match，强制拉取最新内容并刷新缓存；
        否则按 ETag 做条件请求，服务端未变（304）时复用本地缓存。
        """
        self._ensure_config()
        url = self.base_url.rstrip('/') + '/' + path.lstrip('/')
        headers = {
            'Authorization': 'Bearer %s' % self.token,
            'X-Rxlib-Client': self.client_id,
            'X-Rxlib-Version': self.client_version,
        }
        key = self._cache_key(path, params)
        entry = None if force else self._etag_cache.get(key)
        if entry is not None:
            headers['If-None-Match'] = entry['etag']
        client = self._get_client()
        import httpx
        try:
            resp = client.get(url, params=params, headers=headers,
                              timeout=timeout if timeout is not None else self.timeout)
        except httpx.TimeoutException:
            raise RuntimeError('API 请求超时（%s），请稍后重试' % path)
        except httpx.HTTPError as exc:
            raise RuntimeError('API 请求失败（%s）：%s' % (path, exc.__class__.__name__))
        if resp.status_code == 304:
            if entry is not None:
                return entry['data']
            # 无本地缓存却收到 304（异常）：丢弃条件头重取一次。
            return self._get(path, params=params, timeout=timeout, force=True)
        if resp.status_code != 200:
            msg = self._safe_msg(resp)
            if resp.status_code in (401, 403):
                raise ApiAuthError('API 返回 HTTP %d（%s）%s'
                                   % (resp.status_code, path, msg))
            raise RuntimeError('API 返回 HTTP %d（%s）%s'
                               % (resp.status_code, path, msg))
        try:
            body = resp.json()
        except ValueError:
            raise RuntimeError('API 返回内容不是合法 JSON（%s）' % path)
        if not isinstance(body, dict) or body.get('code') != 0:
            raise RuntimeError('API 业务错误（%s）：%s'
                               % (path, self._body_msg(body)))
        data = body.get('data')
        etag = resp.headers.get('ETag') or resp.headers.get('etag')
        if etag:
            self._etag_cache[key] = {'etag': etag, 'data': data}
        return data

    @staticmethod
    def _safe_msg(resp):
        try:
            body = resp.json()
            if isinstance(body, dict) and body.get('msg'):
                return str(body['msg'])
        except ValueError:
            pass
        return ''

    @staticmethod
    def _body_msg(body):
        if isinstance(body, dict) and body.get('msg'):
            return str(body['msg'])
        return '未知错误'

    @staticmethod
    def _normalize_identity(df):
        out = df.copy()
        if 'rxcode' not in out.columns:
            out['rxcode'] = ''
        rx_text = out['rxcode'].where(out['rxcode'].notna(), '').map(
            lambda x: str(x).strip())
        valid_rx = rx_text.map(lambda x: '.' in x)
        code_text = out['code'].where(out['code'].notna(), '').map(
            lambda x: PB.to_code(x)) if 'code' in out.columns else pd.Series(
                '', index=out.index)
        rx_fixed = pd.Series(
            [PB.to_rxcode(PB.to_full(c)) if c else '' for c in code_text],
            index=out.index)
        out['rxcode'] = rx_text.where(valid_rx, rx_fixed).astype(str)
        out['code'] = out['rxcode'].map(
            lambda x: PB.to_code(x) if x else '').astype(str)
        return out

    def load_universe(self, user_id, force=False):
        params = {}
        if user_id is not None:
            if int(user_id) <= 0:
                raise ValueError('user_id 必须为正整数')
            params['user_id'] = int(user_id)
        data = self._get('/universe', params, force=force) or {}
        rows = data.get('rows') or []
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(
                columns=['rxcode', 'code', 'name', 'list_date', 'is_st']), set()
        df = self._normalize_identity(df)
        df['is_st'] = pd.to_numeric(df['is_st'], errors='coerce').fillna(0).astype(int)
        st_set = set(data.get('st') or [])
        st_set |= set(df.loc[df['is_st'] == 1, 'rxcode'])
        return df, st_set

    KLINE_BATCH = 80

    def load_kline(self, codes, start, end):
        base_params = {
            'start': pd.Timestamp(start).strftime('%Y-%m-%d'),
            'end': pd.Timestamp(end).strftime('%Y-%m-%d'),
            'adjust': 'none',
        }
        # 大批量数据按 6 位 code 分批拉取（rxcode 去重后再换算，避免重复取数）
        rx_of = {}
        for c in codes:
            s = str(c)
            full = s if '.' in s and s.split('.')[0] not in ('SH', 'SZ', 'BJ') \
                else PB.to_full(c)
            rx = PB.to_rxcode(full)
            rx_of.setdefault(rx, PB.to_code(rx))
        all_codes = list(rx_of.values())
        chunks = [all_codes[i:i + self.KLINE_BATCH]
                  for i in range(0, len(all_codes), self.KLINE_BATCH)]
        if not chunks:
            chunks = [None]
        kline_timeout = max(float(self.timeout), 60)
        frames = []
        for ch in chunks:
            params = dict(base_params)
            if ch:
                params['codes'] = ','.join(ch)
            data = self._get('/kline', params, timeout=kline_timeout) or {}
            frames.append(pd.DataFrame(data.get('rows') or []))
        cols = ['rxcode', 'code', 'name', 'trade_date', 'open', 'high', 'low',
                'close', 'volume', 'limit_up', 'is_st']
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        df = df.reindex(columns=cols)
        if df.empty:
            return df
        df = self._normalize_identity(df)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        for col in ('open', 'high', 'low', 'close', 'limit_up', 'volume'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['volume'] = df['volume'].fillna(0)
        df['is_st'] = pd.to_numeric(df['is_st'], errors='coerce').fillna(0).astype(int)
        # 补齐与 MySQL load_kline 一致的原始列名，供面板构建直接取用
        df['open_price'] = df['open']
        df['high_price'] = df['high']
        df['low_price'] = df['low']
        df['close_price'] = df['close']
        df['limit_up_price'] = df['limit_up']
        # 服务端已按 [start-60自然日, end] 预热返回，客户端不再裁剪。
        return df.sort_values(['rxcode', 'trade_date']).reset_index(drop=True)

    def load_month(self, ym):
        """拉取单个全市场月片（YYYYMM），只保留该月数据，列结构与 MySQL 链路一致。

        全市场按 month 分片，避免 codes 分批；不落地，由 factor_lab 统一缓存。
        """
        period = pd.Period(ym, freq='M')
        params = {
            'start': period.start_time.strftime('%Y-%m-%d'),
            'end': period.end_time.strftime('%Y-%m-%d'),
            'month': period.strftime('%Y-%m'),
            'adjust': 'none',
        }
        data = self._get('/kline', params,
                         timeout=max(float(self.timeout), 120)) or {}
        df = pd.DataFrame(data.get('rows') or [])
        cols = ['rxcode', 'code', 'name', 'trade_date', 'open', 'high', 'low',
                'close', 'volume', 'limit_up', 'is_st']
        df = df.reindex(columns=cols)
        if df.empty:
            return df
        df = self._normalize_identity(df)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        for col in ('open', 'high', 'low', 'close', 'limit_up', 'volume'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['volume'] = df['volume'].fillna(0)
        df['is_st'] = pd.to_numeric(df['is_st'], errors='coerce').fillna(0).astype(int)
        # 服务端 warmup 会带入上月数据，只保留目标月
        month_mask = df['trade_date'].dt.to_period('M') == period
        df = df.loc[month_mask]
        df['open_price'] = df['open']
        df['high_price'] = df['high']
        df['low_price'] = df['low']
        df['close_price'] = df['close']
        df['limit_up_price'] = df['limit_up']
        return df.sort_values(['rxcode', 'trade_date']).reset_index(drop=True)

    def benchmark(self, code, start, end):
        params = {
            'code': str(PB.to_code(code or DEFAULT_BENCHMARK)),
            'start': pd.Timestamp(start).strftime('%Y-%m-%d'),
            'end': pd.Timestamp(end).strftime('%Y-%m-%d'),
        }
        data = self._get('/benchmark', params) or {}
        rows = data.get('rows') or []
        if not rows:
            return pd.Series(dtype=float)
        df = pd.DataFrame(rows)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna().sort_values('trade_date')
        return df.set_index('trade_date')['close']

    def intraday_snapshot(self, date=None, force=False):
        """拉取盘中快照；返回 (df, trade_date, tick_time)。

        df 与日线同构（含 open_price 等原始名），供 factor_lab 直接拼接；
        盘中接口没有 limit_up，由调用方按板块近似。force=True 强制拉最新（绕过 ETag）。
        """
        params = None
        if date is not None:
            params = {'date': pd.Timestamp(date).strftime('%Y-%m-%d')}
        data = self._get('/intraday/snapshot', params, force=force) or {}
        rows = data.get('rows') or []
        trade_date = data.get('trade_date')
        tick_time = data.get('tick_time')
        cols = ['rxcode', 'code', 'name', 'trade_date', 'open',
                'high', 'low', 'close', 'pre_close',
                'volume', 'amount', 'turnover_rate', 'is_st', 'tick_time']
        df = pd.DataFrame(rows)
        df = df.reindex(columns=cols)
        if df.empty:
            return df, trade_date, tick_time
        df = self._normalize_identity(df)
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        for col in ('open', 'high', 'low', 'close',
                    'pre_close', 'turnover_rate'):
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
        df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
        df['is_st'] = pd.to_numeric(df['is_st'], errors='coerce').fillna(
            0).astype(int)
        df['open_price'] = df['open']
        df['high_price'] = df['high']
        df['low_price'] = df['low']
        df['close_price'] = df['close']
        df['pre_close_price'] = df['pre_close']
        return df.sort_values(['rxcode', 'trade_date']).reset_index(
            drop=True), trade_date, tick_time

    def intraday_latest(self):
        try:
            meta = self.meta() or {}
        except Exception:
            return None
        if not meta.get('allow_intraday'):
            return None
        value = meta.get('intraday_latest_date')
        return pd.Timestamp(value).date() if value else None

    def latest_trade_date(self):
        data = self._get('/latest-trade-date') or {}
        value = data.get('trade_date')
        if not value:
            raise RuntimeError('API 未返回 trade_date')
        return pd.Timestamp(value).date()


def get_provider(kind='mysql', **kw):
    if kind == 'mysql':
        return MysqlProvider(**kw)
    if kind == 'api':
        return ApiProvider(**kw)
    if kind == 'import':
        return ImportProvider(**kw)
    raise ValueError('unknown provider kind %s' % kind)


class ImportProvider(DataProvider):
    """本地 CSV 数据提供层；把按月导出的日线 K 线当独立数据源。

    与 mysql/api 不同，import 完全离线：从配置的 data_dir 读取按月组织的
    CSV（daily_YYYYMM.csv），经统一 normalize 后由 factor_lab 直接读源，
    不经过共享月片缓存，保证新导入的数据即时生效。回测/基准/面板上层零改动。

    约定 CSV 列（大小写不敏感，缺省列自动补齐，多余列忽略）：
      trade_date    ：YYYY-MM-DD（也兼容 YYYY/MM/DD），必有
      code          ：6 位代码或带 SH/SZ/BJ 前缀，必有
      name          ：可缺省（缺省填 code）
      open/high/low/close ：价格，必有
      volume        ：可缺省（缺省填 0）
      limit_up      ：涨停价，可缺省（缺省填 0）
      is_st         ：是否 ST，可缺省（缺省填 0）
    一年一月对应一个文件，如 data/daily_202610.csv。
    """
    name = 'import'
    FILE_PATTERN = 'daily_%s.csv'

    def __init__(self, data_dir=None):
        if data_dir is None:
            import platform_env
            data_dir = platform_env.get_import_config().get('data_dir')
        self.data_dir = (data_dir or '').strip()

    # ---------- 内部工具 ----------

    def _month_path(self, ym):
        return os.path.join(self.data_dir, self.FILE_PATTERN % ym)

    def _available_months(self):
        out = []
        if not os.path.isdir(self.data_dir):
            return out
        for fn in sorted(os.listdir(self.data_dir)):
            if fn.startswith('daily_') and fn.endswith('.csv'):
                ym = fn[len('daily_'):-len('.csv')]
                if len(ym) == 6 and ym.isdigit():
                    out.append(ym)
        return out

    def _parse_month(self, ym):
        """读取单月 CSV，做基础 normalize，返回 [rxcode, code, name, trade_date,
        open, high, low, close, volume, limit_up, is_st]（不含 *_price 别名）。"""
        path = self._month_path(ym)
        if not os.path.exists(path):
            return pd.DataFrame()
        try:
            # dtype=str：避免 code 类数字列丢前导零（000001 -> 1）；
            # 数值列随后统一 to_numeric 转换。
            df = pd.read_csv(path, dtype=str)
        except Exception as e:
            raise RuntimeError('读取导入 CSV 失败（%s）：%s' % (path, e))
        if df.empty:
            return df
        low = {}
        for col in df.columns:
            k = str(col).strip().lower()
            low.setdefault(k, col)
        df = df.rename(columns={v: k for k, v in low.items()}).copy()
        if 'code' not in df.columns:
            raise RuntimeError('导入 CSV 缺少 code 列（%s）' % path)
        if 'trade_date' not in df.columns:
            raise RuntimeError('导入 CSV 缺少 trade_date 列（%s）' % path)
        df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce')
        df = df.dropna(subset=['trade_date', 'code'])
        if df.empty:
            return df
        for col in ('open', 'high', 'low', 'close'):
            if col not in df.columns:
                raise RuntimeError('导入 CSV 缺少 %s 列（%s）' % (col, path))
            df[col] = pd.to_numeric(df[col], errors='coerce')
        if 'volume' not in df.columns:
            df['volume'] = np.nan
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
        if 'name' not in df.columns:
            df['name'] = ''
        df['name'] = df['name'].where(df['name'].notna(), '').astype(str)
        if 'limit_up' not in df.columns:
            df['limit_up'] = np.nan
        df['limit_up'] = pd.to_numeric(df['limit_up'], errors='coerce')
        if 'is_st' not in df.columns:
            df['is_st'] = 0
        df['is_st'] = pd.to_numeric(df['is_st'], errors='coerce').fillna(0).astype(int)
        return df

    def _stock_month(self, ym):
        """读单月 CSV 并补全 rxcode/code/_*price，供上层直接拼板。"""
        df = self._parse_month(ym)
        if df.empty:
            return df
        df = ApiProvider._normalize_identity(df)
        df = df.reindex(columns=['rxcode', 'code', 'name', 'trade_date',
                                 'open', 'high', 'low', 'close', 'volume',
                                 'limit_up', 'is_st'])
        df['open_price'] = df['open']
        df['high_price'] = df['high']
        df['low_price'] = df['low']
        df['close_price'] = df['close']
        df['limit_up_price'] = df['limit_up']
        return df.drop_duplicates(subset=['rxcode', 'trade_date'], keep='last')

    # ---------- DataProvider 接口 ----------

    def load_month(self, ym):
        """单月全市场分片，列结构与 MySQL/API 链路一致（含 *_price 别名）。"""
        period = pd.Period(str(ym), freq='M')
        df = self._stock_month(period.strftime('%Y%m'))
        if df.empty:
            return df
        mask = df['trade_date'].dt.to_period('M') == period
        df = df.loc[mask]
        return df.sort_values(['rxcode', 'trade_date']).reset_index(drop=True)

    def load_universe(self, user_id, force=False):
        months = self._available_months()
        st_set = set()
        if not months:
            return pd.DataFrame(
                columns=['rxcode', 'code', 'name', 'list_date', 'is_st']), st_set
        frames = [self._parse_month(m) for m in months]
        sub = pd.concat(frames, ignore_index=True)
        sub = ApiProvider._normalize_identity(sub)
        uni = (sub.sort_values('trade_date').groupby(
            'rxcode', as_index=False).agg(code=('code', 'last'),
                                          name=('name', 'last'),
                                          is_st=('is_st', 'max')))
        uni['list_date'] = pd.NaT
        st_set = set(uni.loc[uni['is_st'] == 1, 'rxcode'])
        return uni, st_set

    def load_kline(self, codes, start, end):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        lo = (start - timedelta(days=WARMUP_DAYS)).to_period('M')
        hi = end.to_period('M')
        frames = []
        cur = lo
        while cur <= hi:
            df = self._stock_month(cur.strftime('%Y%m'))
            if not df.empty:
                frames.append(df)
            cur += 1
        k = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if k.empty:
            return k
        wanted = {str(c).strip() for c in codes}
        mask = k['code'].isin(wanted) | k['rxcode'].isin(wanted)
        k = k.loc[mask]
        k = k[(k['trade_date'] >= lo.to_timestamp()) & (k['trade_date'] <= end)]
        return k.sort_values(['rxcode', 'trade_date']).reset_index(drop=True)

    def benchmark(self, code, start, end):
        """从全部月文件里取该代码的收盘价序列，用作回测基准。"""
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        wanted = PB.to_rxcode(to_bench_full(code))
        by_date = {}
        lo = (start - timedelta(days=2)).to_period('M')
        hi = end.to_period('M')
        cur = lo
        while cur <= hi:
            df = self._stock_month(cur.strftime('%Y%m'))
            if not df.empty:
                t = df[df['rxcode'] == wanted]
                for d, c in zip(t['trade_date'], t['close']):
                    by_date[d] = c
            cur += 1
        if not by_date:
            return pd.Series(dtype=float)
        s = pd.Series(by_date).sort_index()
        s.index = pd.to_datetime(s.index)
        return s.astype(float)

    def latest_trade_date(self):
        best = None
        for m in self._available_months():
            df = self._parse_month(m)
            if df.empty:
                continue
            md = df['trade_date'].max()
            if pd.isna(md):
                continue
            md = pd.Timestamp(md)
            if best is None or md > best:
                best = md
        if best is None:
            raise RuntimeError('import 数据目录未配置或没有任何数据文件')
        return best.date()

    def intraday_snapshot(self, date=None, force=False):
        return pd.DataFrame(), None, None

    def intraday_latest(self):
        return None
