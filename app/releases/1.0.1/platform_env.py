# coding: utf-8
"""
platform_env —— rxlib 便携平台的运行环境定位。

负责解析发布根目录（RX_HOME）、外部配置文件与数据目录，使应用代码
不依赖固定盘符、不写死数据库地址：

  RX_HOME 默认取本文件向上两级（发布形态 app/current -> 根目录）；
  开发形态（quant 目录）下回退到当前目录，保持与历史行为一致。

可通过环境变量 RXLIB_HOME 显式覆盖。
"""
import os

APP_VERSION = '1.0.1'

_CONFIG_NAME = 'config.ini'
_CONFIG_VERSION = 1


def _detect_home():
    env = os.environ.get('RXLIB_HOME')
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    here = os.path.dirname(os.path.abspath(__file__))
    up2 = os.path.abspath(os.path.join(here, '..', '..'))
    if os.path.isdir(os.path.join(up2, 'config')) and \
            os.path.isdir(os.path.join(up2, 'runtime')):
        return up2
    return here


RX_HOME = _detect_home()
CONFIG_DIR = os.path.join(RX_HOME, 'config')
DATA_DIR = os.path.join(RX_HOME, 'data')
CACHE_DIR = os.path.join(DATA_DIR, 'cache')
RESULTS_DIR = os.path.join(DATA_DIR, 'results')
LOGS_DIR = os.path.join(RX_HOME, 'logs')

DEFAULT_DB = {
    'host': '127.0.0.1',
    'port': 3306,
    'database': 'rxdata',
    'user': 'root',
    'password': '123456',
}

DEFAULT_API = {
    'base_url': '',
    'token': '',
    'timeout': 30,
    'verify_ssl': True,
}

DEFAULT_IMPORT = {
    'data_dir': '',
}


def _parse_ini(path):
    import configparser
    cp = configparser.ConfigParser()
    cp.read(path, encoding='utf-8-sig')
    if not cp.has_section('db'):
        return None
    sec = cp['db']
    cfg = {
        'host': sec.get('host', DEFAULT_DB['host']),
        'port': sec.getint('port', fallback=DEFAULT_DB['port']),
        'database': sec.get('database', DEFAULT_DB['database']),
        'user': sec.get('user', DEFAULT_DB['user']),
        'password': sec.get('password', DEFAULT_DB['password']),
    }
    return cfg


def write_example_config(path=None):
    path = path or os.path.join(CONFIG_DIR, 'config.example.ini')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    d = DEFAULT_DB
    with open(path, 'w', encoding='utf-8') as f:
        f.write('# rxlib 平台配置模板（复制为 config.ini 后修改）\n')
        f.write('[meta]\nconfig_version = %d\n\n' % _CONFIG_VERSION)
        f.write('[db]\n')
        f.write('host = %s\n' % d['host'])
        f.write('port = %d\n' % d['port'])
        f.write('database = %s\n' % d['database'])
        f.write('user = %s\n' % d['user'])
        f.write('password = %s\n' % d['password'])
    return path


def ensure_dirs():
    for d in (CONFIG_DIR, DATA_DIR, CACHE_DIR, RESULTS_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)


def get_db_config():
    ensure_dirs()
    ini = os.path.join(CONFIG_DIR, _CONFIG_NAME)
    if os.path.exists(ini):
        cfg = _parse_ini(ini)
        if cfg is not None:
            return cfg
    write_example_config()
    return dict(DEFAULT_DB)


def save_db_config(cfg):
    import configparser
    ensure_dirs()
    ini = os.path.join(CONFIG_DIR, _CONFIG_NAME)
    cp = configparser.ConfigParser()
    cp.read(ini, encoding='utf-8-sig')
    if not cp.has_section('meta'):
        cp.add_section('meta')
    cp.set('meta', 'config_version', str(_CONFIG_VERSION))
    if not cp.has_section('db'):
        cp.add_section('db')
    merged = dict(DEFAULT_DB)
    merged.update(cfg or {})
    for key in ('host', 'database', 'user', 'password'):
        cp.set('db', key, str(merged[key]))
    cp.set('db', 'port', str(int(merged.get('port', DEFAULT_DB['port']))))
    with open(ini, 'w', encoding='utf-8') as f:
        cp.write(f)
    return ini


def _parse_api(cp):
    sec = cp['api'] if cp.has_section('api') else {}

    def _get(key, default):
        if hasattr(sec, 'get'):
            return sec.get(key, default)
        return default

    timeout = DEFAULT_API['timeout']
    try:
        timeout = int(_get('timeout', timeout))
    except (TypeError, ValueError):
        timeout = DEFAULT_API['timeout']
    verify = str(_get('verify_ssl', DEFAULT_API['verify_ssl'])).strip().lower()
    verify_ssl = verify not in ('0', 'false', 'no', 'off')
    return {
        'base_url': _get('base_url', DEFAULT_API['base_url']),
        'token': _get('token', DEFAULT_API['token']),
        'timeout': timeout,
        'verify_ssl': verify_ssl,
    }


def get_api_config():
    """读取 [api] 配置；环境变量 RXLIB_API_BASE_URL / RXLIB_API_TOKEN 优先。"""
    import configparser
    ensure_dirs()
    ini = os.path.join(CONFIG_DIR, _CONFIG_NAME)
    cfg = dict(DEFAULT_API)
    if os.path.exists(ini):
        cp = configparser.ConfigParser()
        cp.read(ini, encoding='utf-8-sig')
        if cp.has_section('api'):
            cfg.update(_parse_api(cp))
    env_base = os.environ.get('RXLIB_API_BASE_URL')
    env_token = os.environ.get('RXLIB_API_TOKEN')
    if env_base:
        cfg['base_url'] = env_base
    if env_token:
        cfg['token'] = env_token
    return cfg


def save_api_config(cfg):
    import configparser
    ensure_dirs()
    ini = os.path.join(CONFIG_DIR, _CONFIG_NAME)
    cp = configparser.ConfigParser()
    cp.read(ini, encoding='utf-8-sig')
    if not cp.has_section('meta'):
        cp.add_section('meta')
    cp.set('meta', 'config_version', str(_CONFIG_VERSION))
    if not cp.has_section('api'):
        cp.add_section('api')
    merged = dict(DEFAULT_API)
    merged.update(cfg or {})
    cp.set('api', 'base_url', str(merged.get('base_url', '') or ''))
    cp.set('api', 'token', str(merged.get('token', '') or ''))
    cp.set('api', 'timeout', str(int(merged.get('timeout', DEFAULT_API['timeout']))))
    cp.set('api', 'verify_ssl',
           'true' if merged.get('verify_ssl', True) else 'false')
    with open(ini, 'w', encoding='utf-8') as f:
        cp.write(f)
    return ini

def _to_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() not in ('0', 'false', 'no', 'off')


def get_cache_config():
    """读取 [cache] 配置：面板磁盘持久化开关，默认关闭。"""
    import configparser
    ensure_dirs()
    ini = os.path.join(CONFIG_DIR, _CONFIG_NAME)
    # 面板磁盘缓存默认开启：首次构建指标后落盘，同 (股票池,面板版本,区间)
    # 再次运行直接读盘、跳过 calc_breitstein 重算，大幅减少"正在计算指标"耗时。
    enabled = True
    if os.path.exists(ini):
        cp = configparser.ConfigParser()
        cp.read(ini, encoding='utf-8-sig')
        if cp.has_section('cache'):
            enabled = _to_bool(cp['cache'].get('panel_disk_cache', True),
                               True)
    return {'panel_disk_cache': enabled}


def save_cache_config(cfg):
    import configparser
    ensure_dirs()
    ini = os.path.join(CONFIG_DIR, _CONFIG_NAME)
    cp = configparser.ConfigParser()
    cp.read(ini, encoding='utf-8-sig')
    if not cp.has_section('meta'):
        cp.add_section('meta')
    cp.set('meta', 'config_version', str(_CONFIG_VERSION))
    if not cp.has_section('cache'):
        cp.add_section('cache')
    enabled = _to_bool((cfg or {}).get('panel_disk_cache', False), False)
    cp.set('cache', 'panel_disk_cache', 'true' if enabled else 'false')
    with open(ini, 'w', encoding='utf-8') as f:
        cp.write(f)
    return ini


def get_import_config():
    """读取 [import] 配置：本地 CSV 导入数据源的目录。

    未配置时回退到默认导入目录 data/import；读取阶段不落盘，保存时才写入。
    """
    import configparser
    ensure_dirs()
    ini = os.path.join(CONFIG_DIR, _CONFIG_NAME)
    cfg = dict(DEFAULT_IMPORT)
    if os.path.exists(ini):
        cp = configparser.ConfigParser()
        cp.read(ini, encoding='utf-8-sig')
        if cp.has_section('import'):
            cfg['data_dir'] = cp['import'].get(
                'data_dir', DEFAULT_IMPORT['data_dir'])
    env_dir = os.environ.get('RXLIB_IMPORT_DATA_DIR')
    if env_dir:
        cfg['data_dir'] = env_dir
    if not (cfg.get('data_dir') or '').strip():
        cfg['data_dir'] = os.path.join(DATA_DIR, 'import')
    return cfg


def save_import_config(cfg):
    import configparser
    ensure_dirs()
    ini = os.path.join(CONFIG_DIR, _CONFIG_NAME)
    cp = configparser.ConfigParser()
    cp.read(ini, encoding='utf-8-sig')
    if not cp.has_section('meta'):
        cp.add_section('meta')
    cp.set('meta', 'config_version', str(_CONFIG_VERSION))
    if not cp.has_section('import'):
        cp.add_section('import')
    merged = dict(DEFAULT_IMPORT)
    merged.update(cfg or {})
    cp.set('import', 'data_dir', str(merged.get('data_dir') or '').strip())
    with open(ini, 'w', encoding='utf-8') as f:
        cp.write(f)
    return ini
