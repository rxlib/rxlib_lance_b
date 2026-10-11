# coding: utf-8
"""探测本地 MySQL 是否含真实 2026-06 测试股票池行情，并导出 data/import/daily_202606.csv。

用法（与平台运行环境一致）：
  $env:RXLIB_HOME = <repo root>
  $env:PYTHONPATH  = <runtime>/pylibs;<AppDir>
  运行本脚本后，依据输出决定导出。
"""
import os
import sys
import datetime

import pymysql
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
# 脚本位于 app/releases/1.0.1/ ，上溯到仓库根
for _ in range(3):
    if os.path.isdir(os.path.join(REPO_ROOT, 'app')):
        break
    REPO_ROOT = os.path.dirname(REPO_ROOT)
print('REPO_ROOT =', REPO_ROOT)

try:
    import platform_env
    print('platform_env.RX_HOME =', platform_env.RX_HOME)
    print('get_db_config()     =', platform_env.get_db_config())
    IMPORT_DIR = os.path.join(platform_env.RX_HOME, 'data', 'import')
except Exception as e:
    print('platform_env 异常：', e)
    IMPORT_DIR = os.path.join(REPO_ROOT, 'data', 'import')

TEST_CODES = (
    '688002', '688008', '688009', '688012', '688027',
    '688036', '688041', '688047', '688072', '688082',
    '688099', '688111', '688120', '688122', '688126',
    '688169', '688183', '688187', '688188', '688213',
    '688220', '688223', '688249', '688256', '688271',
    '688303', '688347', '688361', '688375', '688396',
    '688469', '688472', '688498', '688506', '688521',
    '688525', '688568', '688578', '688599', '688629',
    '688702', '688728', '688729', '688775', '688777',
    '688795', '688802', '688820', '688981', '689009',
)
MONTH = '202606'
FULLS = ['%s.%s' % (c, 'SH' if c.startswith(('6', '9')) else 'SZ')
         for c in TEST_CODES]


def candidates():
    """返回候选 DB 配置列表，依序尝试连接。"""
    out = []
    try:
        out.append(('loaded-config', platform_env.get_db_config()))
    except Exception:
        pass
    try:
        root_ini = os.path.join(REPO_ROOT, 'config', 'config.ini')
        import configparser
        cp = configparser.ConfigParser()
        cp.read(root_ini, encoding='utf-8')
        if cp.has_section('db'):
            out.append(('root-config.ini', {
                'host': cp.get('db', 'host'),
                'port': int(cp.get('db', 'port', fallback=3306)),
                'database': cp.get('db', 'database'),
                'user': cp.get('db', 'user'),
                'password': cp.get('db', 'password'),
            }))
    except Exception as e:
        print('解析 root config.ini 失败：', e)
    out.append(('platform-default', {
        'host': '127.0.0.1', 'port': 3306,
        'database': 'rxdata', 'user': 'root', 'password': '123456',
    }))
    return out


def probe(cfg, name):
    print('---- 尝试数据源：%s -> %s@%s:%s/%s'
          % (name, cfg['user'], cfg['host'], cfg.get('port', 3306),
             cfg['database']))
    try:
        conn = pymysql.connect(
            host=cfg['host'], port=int(cfg.get('port', 3306)),
            database=cfg['database'], user=cfg['user'],
            password=cfg['password'], charset='utf8mb4', connect_timeout=5)
    except Exception as e:
        print('  连接失败：', e)
        return None
    try:
        cur = conn.cursor()
        cur.execute("SHOW TABLES LIKE 'stock_daily_%s'" % MONTH)
        if cur.fetchone() is None or True:  # 统一再查一次全量月表清单
            pass
        cur.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                    ('stock_daily_%s' % MONTH,))
        exists = cur.fetchone()[0] > 0
        print('  stock_daily_%s 表存在：%s' % (MONTH, exists))
        if not exists:
            return None
        cur.execute("SELECT COUNT(*) FROM stock_daily_%s" % MONTH)
        total_rows = cur.fetchone()[0]
        print('  月表总行数：', total_rows)
        cur.execute(
            "SELECT full_code, COUNT(*), MIN(trade_date), MAX(trade_date) "
            "FROM stock_daily_%s "
            "WHERE full_code IN (%s) "
            "GROUP BY full_code" %
            (MONTH, ','.join(['%s'] * len(FULLS))), FULLS)
        rows = cur.fetchall()
        print('  命中测试股记录：%d 组' % len(rows))
        for r in rows:
            print('    ', r)
        # 取股票名
        cur.execute(
            "SELECT full_code, name FROM stock_base WHERE full_code IN (%s)"
            % (','.join(['%s'] * len(FULLS))), FULLS)
        names = {r[0]: r[1] for r in cur.fetchall()}
        print('  stock_base 名称：', names)
        if not rows:
            return None
        return {'conn': conn, 'total_rows': total_rows,
                'matched_rows': rows, 'names': names}
    finally:
        conn.close()


def export(cfg, name, info, import_dir):
    conn = pymysql.connect(
        host=cfg['host'], port=int(cfg.get('port', 3306)),
        database=cfg['database'], user=cfg['user'],
        password=cfg['password'], charset='utf8mb4')
    try:
        start = datetime.datetime(2026, 6, 1)
        end = datetime.datetime(2026, 6, 30)
        sql = ("SELECT full_code, trade_date, open_price, high_price, "
               "low_price, close_price, volume, limit_up_price, is_st "
               "FROM stock_daily_%s WHERE full_code IN (%s) "
               "AND trade_date BETWEEN %%s AND %%s" %
               (MONTH, ','.join(['%s'] * len(FULLS))))
        df = pd.read_sql(sql, conn, params=FULLS + [start, end])
    finally:
        conn.close()
    if df.empty:
        print('无 2026-06 间数据，不导出。')
        return False
    df = df.rename(columns={
        'open_price': 'open', 'high_price': 'high', 'low_price': 'low',
        'close_price': 'close', 'limit_up_price': 'limit_up'})
    df['code'] = df['full_code'].str.split('.').str[0]
    names = info['names']
    df['name'] = df['full_code'].map(lambda x: names.get(x, ''))
    df['trade_date'] = pd.to_datetime(df['trade_date']).dt.strftime('%Y-%m-%d')
    out = df[['trade_date', 'code', 'name', 'open', 'high', 'low', 'close',
              'volume', 'limit_up', 'is_st']].copy()
    # 归一化：价格/成交量转数值、空值剔除；st/trade_date 尽量保留
    for c in ['open', 'high', 'low', 'close', 'volume', 'limit_up']:
        out[c] = pd.to_numeric(out[c], errors='coerce')
    out['is_st'] = pd.to_numeric(out['is_st'], errors='coerce') == 1
    out = out.dropna(subset=['open', 'high', 'low', 'close'])
    out = out.sort_values(['code', 'trade_date'], kind='mergesort')
    os.makedirs(import_dir, exist_ok=True)
    path = os.path.join(import_dir, 'daily_%s.csv' % MONTH)
    out.to_csv(path, index=False, encoding='utf-8')
    print('已导出 %d 行 -> %s' % (len(out), path))
    per_code = out['code'].value_counts()
    print('每股行数：\n%s' % per_code.to_string())
    return True


def main():
    selected = None
    for name, cfg in candidates():
        res = probe(cfg, name)
        if res:
            selected = (name, cfg, res)
            break
    if not selected:
        print('\n结论：未在任何候选数据源发现 stock_daily_%s 真实数据。' % MONTH)
        sys.exit(2)
    name, cfg, info = selected
    print('\n使用数据源：%s 生成真实 2026-06 数据。' % name)
    export(cfg, name, info, IMPORT_DIR)


if __name__ == '__main__':
    main()