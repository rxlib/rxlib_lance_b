# coding: utf-8
"""
local_settings —— rxlib 本地设置与自选池管理。

只读写本地配置文件，不碰任何数据库表：
  <RX_HOME>/config/_factorlab_settings.json：登录密码 / 平台标题 / 面板版本；
  <RX_HOME>/config/config.ini              ：数据库连接配置（唯一来源）；
  <RX_HOME>/config/watchlists/*.csv        ：用户自建股票池（code 6 位, name）。

发布形态下这些文件位于 app 目录之外，升级版本不会被覆盖；
开发形态下回退到 quant 目录，并自动沿用历史文件。
"""
import os
import json
import shutil
import hmac
import base64
import hashlib
import secrets

import pandas as pd

import rx_core as PB
import platform_env
import sql_system as SQL

_HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS_FILE = os.path.join(platform_env.CONFIG_DIR, '_factorlab_settings.json')
WATCH_DIR = os.path.join(platform_env.CONFIG_DIR, 'watchlists')
_LEGACY_SETTINGS_FILE = os.path.join(_HERE, '_factorlab_settings.json')
_LEGACY_WATCH_DIR = os.path.join(_HERE, 'watchlists')

DEFAULT_TITLE = 'rxlib 量化研究平台'
DEFAULT_PASSWORD = 'rxlib'

PBKDF2_ROUNDS = 200_000
COOKIE_NAME = 'rxlib_auth'
COOKIE_DAYS = 30

MIN_PASSWORD_LEN = 4
MAX_PASSWORD_LEN = 64


def _six(c):
    return PB.to_code(c)


def _migrate_legacy_settings():
    if os.path.exists(SETTINGS_FILE) or not os.path.exists(_LEGACY_SETTINGS_FILE):
        return
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    try:
        shutil.copyfile(_LEGACY_SETTINGS_FILE, SETTINGS_FILE)
    except Exception:
        return
    try:
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            legacy = json.load(f)
        if isinstance(legacy.get('db'), dict):
            platform_env.save_db_config(legacy['db'])
    except Exception:
        pass


def _migrate_legacy_watchlists():
    if not os.path.isdir(_LEGACY_WATCH_DIR):
        return
    os.makedirs(WATCH_DIR, exist_ok=True)
    for name in os.listdir(_LEGACY_WATCH_DIR):
        if not name.endswith('.csv'):
            continue
        dst = os.path.join(WATCH_DIR, name)
        if not os.path.exists(dst):
            try:
                shutil.copyfile(os.path.join(_LEGACY_WATCH_DIR, name), dst)
            except Exception:
                pass


def load_settings():
    platform_env.ensure_dirs()
    _migrate_legacy_settings()
    data = {}
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}
    data.pop('db', None)
    data.setdefault('password', DEFAULT_PASSWORD)
    data.setdefault('title', DEFAULT_TITLE)
    data.setdefault('panel_version', 6)
    if int(data.get('panel_version', 6)) < 6:
        data['panel_version'] = 6
    return data


def save_settings(data):
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    data = dict(data)
    data.pop('db', None)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_db_config():
    return platform_env.get_db_config()


def get_panel_version():
    """读取面板缓存版本；代码结构版本前进时自动前向同步。

    面板结构（指标字段、可用窗口）变更后 factor_lab.PANEL_VERSION 会递增。
    若本机设置值落后于它，这里写回最新值，使旧面板缓存判定失效并一次性重建，
    避免"磁盘缓存结构与新代码不匹配"（例如新增均线列在旧面板中取不到）而报错。
    只向上同步：手动调大版本号强制重建的用法不受影响。
    """
    try:
        cur = int(load_settings().get('panel_version', 6))
    except Exception:
        cur = 6
    try:
        import factor_lab as fl
        latest = int(fl.PANEL_VERSION)
    except Exception:
        return cur
    if cur >= latest:
        return cur
    try:
        data = load_settings()
        data['panel_version'] = latest
        save_settings(data)
    except Exception:
        pass
    return latest


def hash_password(pwd):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', str(pwd).encode('utf-8'),
                             salt, PBKDF2_ROUNDS)
    return 'pbkdf2$%d$%s$%s' % (
        PBKDF2_ROUNDS,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode())


def verify_password(pwd, stored=None):
    if stored is None:
        stored = str(load_settings()['password'])
    stored = str(stored)
    if stored.startswith('pbkdf2$'):
        try:
            _, rounds, salt_b, dk_b = stored.split('$')
            salt = base64.b64decode(salt_b)
            expect = base64.b64decode(dk_b)
            dk = hashlib.pbkdf2_hmac('sha256', str(pwd).encode('utf-8'),
                                     salt, int(rounds))
            return hmac.compare_digest(dk, expect)
        except Exception:
            return False
    return hmac.compare_digest(str(pwd), stored)


def validate_password(pwd):
    pwd = '' if pwd is None else str(pwd)
    if pwd != pwd.strip():
        return '密码首尾不能包含空格。'
    if len(pwd) < MIN_PASSWORD_LEN:
        return '密码长度至少 %d 位。' % MIN_PASSWORD_LEN
    if len(pwd) > MAX_PASSWORD_LEN:
        return '密码长度不能超过 %d 位。' % MAX_PASSWORD_LEN
    return ''


def change_password(old_pwd, new_pwd):
    if not verify_password(old_pwd):
        raise ValueError('当前密码不正确。')
    err = validate_password(new_pwd)
    if err:
        raise ValueError(err)
    data = load_settings()
    data['password'] = hash_password(new_pwd)
    save_settings(data)


def _cookie_secret():
    data = load_settings()
    if not data.get('cookie_secret'):
        data['cookie_secret'] = secrets.token_hex(16)
        save_settings(data)
    return data['cookie_secret']


def rotate_cookie_secret():
    data = load_settings()
    data['cookie_secret'] = secrets.token_hex(16)
    save_settings(data)
    return data['cookie_secret']


def make_auth_token():
    exp = int((pd.Timestamp.utcnow() + pd.Timedelta(days=COOKIE_DAYS)).timestamp())
    sig = hmac.new(_cookie_secret().encode(), str(exp).encode(),
                   hashlib.sha256).hexdigest()
    return '%d.%s' % (exp, sig)


def check_auth_token(token):
    try:
        exp_s, sig = str(token).split('.', 1)
        exp = int(exp_s)
    except Exception:
        return False
    if exp < int(pd.Timestamp.utcnow().timestamp()):
        return False
    expect = hmac.new(_cookie_secret().encode(), exp_s.encode(),
                      hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expect)


def test_connection(db):
    import factor_lab as fl
    conn = PB.connect_replica_db(db)
    try:
        cur = conn.cursor()
        cur.execute(SQL.count_stock_base())
        n = cur.fetchone()[0]
        sample = fl.normalize_code('000001')
    finally:
        conn.close()
    return n


def list_watchlists():
    _migrate_legacy_watchlists()
    if not os.path.isdir(WATCH_DIR):
        return []
    return sorted(f[:-4] for f in os.listdir(WATCH_DIR)
                  if f.endswith('.csv'))


def _path(name):
    return os.path.join(WATCH_DIR, '%s.csv' % name)


def _to_rxcode(value):
    """任意入参（rxcode / 后缀式 / 6 位码）→ 唯一 rxcode，无法识别返回 ''。"""
    full = PB.to_full(value)
    return PB.to_rxcode(full) if full else ''


def load_watchlist(name):
    p = _path(name)
    cols = ['rxcode', 'code', 'name']
    if not os.path.exists(p):
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(p, dtype=str).fillna('')
    if 'rxcode' not in df.columns:
        # 兼容历史文件：从 code 列（6 位码）换算唯一 rxcode
        if 'code' not in df.columns:
            df = df.rename(columns={df.columns[0]: 'code'})
        df['rxcode'] = df['code'].map(_to_rxcode)
    df['rxcode'] = df['rxcode'].astype(str).map(_to_rxcode)
    df['code'] = df['rxcode'].map(lambda r: PB.to_code(r) if r else '')
    for col in cols:
        if col not in df.columns:
            df[col] = ''
    df = df[df['rxcode'] != ''].drop_duplicates('rxcode')
    return df[cols].reset_index(drop=True)


def save_watchlist(name, df):
    if not os.path.isdir(WATCH_DIR):
        os.makedirs(WATCH_DIR)
    out = df.copy()
    src_rx = (out['rxcode'] if 'rxcode' in out.columns
              else pd.Series('', index=out.index, dtype=str))
    src_code = (out['code'] if 'code' in out.columns
                else pd.Series('', index=out.index, dtype=str))
    if 'name' not in out.columns:
        out['name'] = ''
    # 优先使用已携带的 rxcode；否则从 6 位码换算
    out['rxcode'] = [_to_rxcode(r) or _to_rxcode(c)
                     for r, c in zip(src_rx, src_code)]
    out['code'] = out['rxcode'].map(lambda r: PB.to_code(r) if r else '')
    out['name'] = out['name'].astype(str).str.strip()
    out = out[out['rxcode'] != ''].drop_duplicates('rxcode')
    out[['rxcode', 'code', 'name']].to_csv(
        _path(name), index=False, encoding='utf-8-sig')
    return len(out)


def delete_watchlist(name):
    p = _path(name)
    if os.path.exists(p):
        os.remove(p)
