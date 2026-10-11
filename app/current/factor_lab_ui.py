# coding: utf-8
"""
factor_lab_ui —— rxlib Streamlit 界面（唯一前端）。

只做"控件 -> cfg -> 调内核 factor_lab 编程接口 -> 展示"，不复制撮合 / 指标 / 评估逻辑。
数据源经 DataProvider 抽象访问。

启动（依赖装在隔离目录 .pylibs）：
  set PYTHONPATH=d:\\gjdata\\quant\\.pylibs
  D:\\Python\\Python310\\python.exe -m streamlit run d:\\gjdata\\quant\\factor_lab_ui.py
"""
import os
import sys
import json
import datetime as dt

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, '.pylibs'))
sys.path.insert(0, _HERE)

import pandas as pd
import streamlit as st

import factor_lab as fl
import rx_core as PB
import data_provider as dp
import local_settings as ls
import platform_env


# 具名预设的中文展示名（内部键名不变，仅用于界面显示；见文档「6.7 具名预设」对照表）
_PRESET_NAMES = {
    'active_momentum':      '活跃动量（默认）',
    'capitulation_bounce':  '投降反弹',
    'narrow_consolidation': '窄幅盘整',
    'strong_resonance':     '强共振',
}


def _preset_label(cid):
    """预设键名 -> 中文为主、附英文键名的展示名；未知键原样返回。"""
    name = _PRESET_NAMES.get(cid)
    return '%s (%s)' % (name, cid) if name else cid

st.set_page_config(page_title='rxlib 量化研究 Lance B 支撑与阻力五要点', layout='wide',
                   page_icon=os.path.join(_HERE, 'favicon.ico'),
                   initial_sidebar_state='expanded')


# -------------------- 登录门 --------------------

import streamlit.components.v1 as components


def _set_cookie_js(name, value, days):
    return (
        '<script>(function(){'
        'var d=new Date();d.setTime(d.getTime()+%d*864e5);'
        'document.cookie="%s="+encodeURIComponent("%s")+";expires="'
        '+d.toUTCString()+";path=/;SameSite=Lax";'
        '})();</script>' % (days, name, value))


def _clear_cookie_js(name):
    return (
        '<script>(function(){'
        'document.cookie="%s=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/";'
        '})();</script>' % name)


def _logout_redirect_js(name):
    return (
        '<script>(function(){'
        'document.cookie="%s=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/";'
        'var w=0;'
        'var t=setInterval(function(){'
        'w++;'
        'if(!document.cookie.split(";").some(function(c){return c.trim().indexOf("%s=")===0;})||w>=10){'
        'clearInterval(t);'
        'window.location.replace(window.location.pathname);'
        '}'
        '},120);'
        '})();</script>' % (name, name))


MAX_LOGIN_FAILS = 5
LOGIN_LOCK_SECONDS = 300


def _login_until_ts():
    return st.session_state.get('_login_lock_until', 0)


def _login_locked():
    import time
    return _login_until_ts() > time.time()


def _login_lock_remain():
    import time
    return max(0, int(_login_until_ts() - time.time()))


def _do_login():
    pwd = st.session_state.get('_login_pwd', '')
    if ls.verify_password(pwd):
        st.session_state.logged_in = True
        st.session_state.pop('_login_error', None)
        st.session_state.pop('_login_fails', None)
        st.session_state.pop('_login_lock_until', None)
    else:
        import time
        fails = int(st.session_state.get('_login_fails', 0)) + 1
        st.session_state._login_fails = fails
        st.session_state._login_error = True
        if fails >= MAX_LOGIN_FAILS:
            st.session_state._login_lock_until = time.time() + LOGIN_LOCK_SECONDS


def _clear_sensitive_state():
    for k in ('_login_pwd', 'set_pwd_old', 'set_pwd1', 'set_pwd2',
              '_auth_token'):
        st.session_state.pop(k, None)
    for k in ('cfg', 'pool_spec', 'history'):
        st.session_state.pop(k, None)


def _do_logout():
    st.session_state.logged_in = False
    st.session_state._logout_done = True
    _clear_sensitive_state()
    try:
        get_panels.clear()
    except Exception:
        pass


def login_gate():
    is_logout = st.session_state.pop('_logout_done', False)
    if not is_logout and not st.session_state.get('logged_in'):
        token = st.context.cookies.get(ls.COOKIE_NAME)
        if token and ls.check_auth_token(token):
            st.session_state.logged_in = True
            st.session_state.pop('_login_fails', None)
            st.session_state.pop('_login_lock_until', None)
    if st.session_state.pop('_just_logged_in', False):
        components.html(
            _set_cookie_js(ls.COOKIE_NAME, st.session_state.pop('_auth_token'),
                           ls.COOKIE_DAYS), height=0)
    if not is_logout and st.session_state.get('logged_in'):
        return
    st.markdown(
        '<div style="max-width:380px;margin:70px auto;text-align:center">'
        '<h2>%s</h2><p>请输入访问密码</p></div>' % ls.DEFAULT_TITLE,
        unsafe_allow_html=True)
    if is_logout:
        components.html(_logout_redirect_js(ls.COOKIE_NAME), height=0)
        st.success('已退出登录。')
    if _login_locked():
        st.error('连续失败次数过多，请 %d 秒后再试。' % _login_lock_remain())
        st.stop()
    if st.session_state.pop('_login_error', False):
        fails = int(st.session_state.get('_login_fails', 0))
        left = max(0, MAX_LOGIN_FAILS - fails)
        st.error('密码错误，请重试。还可尝试 %d 次。' % left)
    with st.form('login_form', clear_on_submit=True):
        st.text_input('访问密码', type='password', key='_login_pwd',
                      label_visibility='collapsed',
                      placeholder='请输入访问密码')
        st.form_submit_button('登 录', type='primary',
                              use_container_width=True, on_click=_after_click_login)
    st.caption('登录状态默认保留 %d 天；可随时在左侧栏退出。' % ls.COOKIE_DAYS)
    st.stop()


def _after_click_login():
    _do_login()
    if st.session_state.get('logged_in'):
        st.session_state._just_logged_in = True
        st.session_state._auth_token = ls.make_auth_token()


# -------------------- 面板缓存 --------------------

@st.cache_resource(max_entries=4, show_spinner=False)
def get_panels(tag, user_id, start, end, nproc, source, panel_version,
               pool_spec, disk_persist):
    provider = dp.get_provider(source, read_config=ls.get_db_config()) \
        if source == 'mysql' else dp.get_provider(source)
    cal, panels, degraded = fl.load_or_build(
        tag, user_id, pd.Timestamp(start), pd.Timestamp(end), nproc,
        read_config=ls.get_db_config(),
        boards=pool_spec.get('boards'),
        exclude_st=pool_spec.get('exclude_st', False),
        exclude_new_days=pool_spec.get('exclude_new_days'),
        custom_codes=pool_spec.get('custom_codes'),
        panel_version=panel_version, provider=provider,
        disk_persist=bool(disk_persist))
    return cal, panels, provider, degraded


@st.cache_resource(max_entries=4, ttl=120, show_spinner=False)
def get_intraday_panels(tag, user_id, start, nproc, source, panel_version,
                        pool_spec, snap_day):
    """盘中口径面板：历史日线 + 当天快照，重算全部指标（不写日线月片）。

    ttl=120：盘中 tick 每分钟变化会触发全量重算（约 95–145 秒），
    限制为最多 2 分钟重建一次，避免每次 Streamlit rerun 都重算。
    """
    provider = dp.get_provider(source, read_config=ls.get_db_config()) \
        if source == 'mysql' else dp.get_provider(source)
    cal, panels, iinfo = fl.load_or_build_intraday(
        tag, user_id, pd.Timestamp(start), nproc,
        read_config=ls.get_db_config(),
        boards=pool_spec.get('boards'),
        exclude_st=pool_spec.get('exclude_st', False),
        exclude_new_days=pool_spec.get('exclude_new_days'),
        custom_codes=pool_spec.get('custom_codes'),
        panel_version=panel_version, provider=provider,
        day=pd.Timestamp(snap_day))
    return cal, panels, provider, iinfo


@st.cache_data(ttl=300, show_spinner=False)
def latest_trade_date(source, _db_cfg):
    provider = dp.get_provider(source, read_config=_db_cfg) \
        if source == 'mysql' else dp.get_provider(source)
    return provider.latest_trade_date()


def _intraday_for_end(end, latest_close, probe):
    """所选结束日无盘后日线、但当天有盘中快照时，返回该快照日；否则 None。

    纯函数，便于单测：end <= latest_close 走日线且不调用盘中；
    end > latest_close 且当天存在盘中快照才启用盘中口径。
    """
    if latest_close is None or probe is None:
        return None
    end = pd.Timestamp(end)
    if end <= pd.Timestamp(latest_close):
        return None                      # 有盘后日线 -> 走日线，不调盘中
    snap = pd.Timestamp(probe['trade_date'])
    return snap if end == snap else None


@st.cache_data(ttl=60, show_spinner=False)
def api_meta():
    try:
        provider = dp.get_provider('api')
        return provider.meta() or {}
    except Exception:
        return {}


@st.cache_data(ttl=30, show_spinner=False)
def intraday_probe(source, _db_cfg):
    """探测该数据源最新可用的盘中快照。

    返回 {'trade_date': date, 'tick_time': str, 'n_snapshot': int}，无则 None。
    MySQL 直连与 API 模式均适用。
    """
    try:
        provider = dp.get_provider(source, read_config=_db_cfg) \
            if source == 'mysql' else dp.get_provider(source)
        latest = provider.intraday_latest()
        if latest is None:
            return None
        snap_df, snap_date, tick_time = provider.intraday_snapshot(latest)
    except Exception:
        return None
    if snap_df is None or snap_df.empty or snap_date is None:
        return None
    return {'trade_date': pd.Timestamp(snap_date).date(),
            'tick_time': tick_time,
            'n_snapshot': int(snap_df['rxcode'].nunique())}


def panels_or_message():
    s = st.session_state
    tag = s.tag
    # 判定：所选结束日无盘后日线、但当天有盘中快照 -> 自动改用盘中口径。
    # 有盘后日线时完全走日线，不调用盘中数据。
    try:
        latest_close = latest_trade_date(s.source, ls.get_db_config())
    except Exception:
        latest_close = None
    probe = None
    allow = (s.source != 'api') or bool(api_meta().get('allow_intraday', False))
    if allow and latest_close is not None \
            and pd.Timestamp(s.end) > pd.Timestamp(latest_close):
        probe = intraday_probe(s.source, ls.get_db_config())
    snap_day = _intraday_for_end(s.end, latest_close, probe)
    s.intraday_info = probe if snap_day is not None else None
    s.intraday_tag = None if snap_day is None else snap_day.strftime('%Y-%m-%d')
    try:
        with st.spinner('加载/构建面板（首次较慢，之后走缓存）...'):
            if snap_day is not None:
                cal, panels, provider, iinfo = get_intraday_panels(
                    tag, s.user_id, s.start, s.procs, s.source,
                    ls.get_panel_version(), s.pool_spec, snap_day)
                s.intraday_info = iinfo
                rx_info('本次为盘中口径：快照 %s %s，覆盖 %d 只；结果会随快照刷新。'
                        % (iinfo['trade_date'], iinfo['tick_time'] or '-',
                           iinfo['n_snapshot']))
                degraded = None
            else:
                cal, panels, provider, degraded = get_panels(
                    tag, s.user_id, s.start, s.end, s.procs, s.source,
                    ls.get_panel_version(), s.pool_spec,
                    platform_env.get_cache_config().get('panel_disk_cache',
                                                       False))
    except Exception as e:
        rx_error('面板取数失败：%s' % e)
        return None
    if degraded:
        st.markdown('<div class="rxlib-auth-warn">%s</div>' %
                    degraded, unsafe_allow_html=True)
    return cal, panels, provider, tag


# -------------------- 侧边栏：平台标题 + 参数 --------------------

def global_chrome_styles():
    st.markdown(
        '<style>'
        'header[data-testid="stHeader"] .st-emotion-cache-jma0kd.e1yxiy6j5,'
        'header[data-testid="stHeader"] [data-testid="stToolbar"] > div > div:first-child{'
        'display:flex;align-items:center;'
        'padding-left:2rem !important;padding-right:2rem !important;'
        '}'
        'header[data-testid="stHeader"] .st-emotion-cache-jma0kd.e1yxiy6j5::before,'
        'header[data-testid="stHeader"] [data-testid="stToolbar"] > div > div:first-child::before{'
        'content:"rxlib 量化研究 Lance B 支撑与阻力五要点";'
        'font-size:.95rem;font-weight:600;white-space:nowrap;'
        '}'
        '[data-testid="stMainBlockContainer"],'
        '.st-emotion-cache-zy6yx3{'
        'padding:2rem 1rem 10rem !important;'
        '}'
        '[data-testid="stButton"] button,'
        '[data-testid="stDownloadButton"] button,'
        '[data-testid="stFormSubmitButton"] button{'
        'min-height:2rem !important;'
        'border-radius:0.375rem !important;'
        'white-space:nowrap !important;'
        '}'
        '[data-testid="stButton"] button[kind="primary"],'
        '[data-testid="stFormSubmitButton"] button[kind="primary"]{'
        'background-color:#2563EB !important;'
        'border-color:#2563EB !important;'
        '}'
        # 水平单选做成圆角分段控件：未选浅灰、选中蓝，单排不折行
        '[role="radiogroup"][aria-orientation="horizontal"]{'
        'flex-wrap:nowrap !important;'
        'gap:0.375rem !important;'
        '}'
        '[role="radiogroup"][aria-orientation="horizontal"] > div{'
        'flex:0 0 auto;'
        '}'
        '[role="radiogroup"][aria-orientation="horizontal"] label{'
        'border-radius:0.375rem !important;'
        'white-space:nowrap !important;'
        'background:#F2F3F5 !important;'
        'padding:0.3rem 0.7rem !important;'
        'margin:0 !important;'
        'font-size:0.875rem !important;'
        'color:#4B5563 !important;'
        'border:1px solid #E5E7EB;'
        '}'
        # 隐藏自定义圆点，胶囊内只留文字
        '[role="radiogroup"][aria-orientation="horizontal"] label > div > div:first-child{'
        'display:none !important;'
        '}'
        '[role="radiogroup"][aria-orientation="horizontal"] label:has(input:checked){'
        'background:#2563EB !important;'
        'border-color:#2563EB !important;'
        'color:#FFFFFF !important;'
        '}'
        '.rx-run-btn p{'
        'font-size:0.75rem !important;'
        '}'
        '[data-testid="stSidebar"] h3{'
        'font-size:1rem;'
        '}'
        '[data-testid="stAppDeployButton"]{'
        'display:none !important;'
        '}'
        '[data-testid="stMainMenuItem-rerun"],'
        '[data-testid="stMainMenuItem-autoRerun"],'
        '[data-testid="stMainMenuItem-clearCache"],'
        '[data-testid="stMainMenuItem-print"],'
        '[data-testid="stMainMenuItem-recordScreencast"],'
        '[data-testid="stMainMenuItem-about"]{'
        'display:none !important;'
        '}'
        '[data-testid="stMainMenuList"] ~ div{'
        'display:none !important;'
        '}'
        '[data-testid="stMainMenuPopover"] [data-testid="stMainMenuDivider"]{'
        'display:none !important;'
        '}'
        '[data-testid="stThemeSwitcher"]{'
        'flex-direction:column !important;'
        '}'
        '[data-testid="stThemeSwitcher"] button{'
        'flex:0 0 auto !important;'
        'width:100%;'
        'flex-direction:row !important;'
        'justify-content:flex-start !important;'
        '}'
        '.rxlib-info{'
        'background-color:rgba(37, 99, 235, 0.1);'
        'color:rgb(37, 99, 235);'
        'font-size:14px;'
        'line-height:1.5;'
        'padding:0.5rem;'
        'border-radius:0.5rem;'
        'word-break:break-word;'
        '}'
        '.rxlib-info p{'
        'margin:0;'
        'color:rgb(37, 99, 235);'
        'font-size:14px;'
        '}'
        '.rxlib-error{'
        'background-color:rgba(242, 87, 70, 0.1);'
        'color:rgb(242, 87, 70);'
        'font-size:14px;'
        'line-height:1.5;'
        'padding:0.5rem;'
        'border-radius:0.5rem;'
        'word-break:break-word;'
        '}'
        '.rxlib-error p{'
        'margin:0;'
        'color:rgb(242, 87, 70);'
        'font-size:14px;'
        '}'
        '[data-testid="stMainBlockContainer"]:has(.rxlib-help-marker) h2{'
        'font-size:1.25rem;'
        '}'
        '[data-testid="stTabPanel"]:has(.rxlib-watch-marker) h3{'
        'font-size:1.2rem;'
        '}'
        '.rxlib-auth-warn{'
        'margin:.2rem 0 .35rem;padding:.28rem .6rem;'
        'font-size:.8rem;line-height:1.3;border-radius:.35rem;'
        'background:rgba(255,193,7,.14);'
        'border:1px solid rgba(255,193,7,.55);'
        'color:inherit;'
        '}'
        '.rxlib-auth-warn p{margin:0;font-size:.8rem;}'
        '</style>', unsafe_allow_html=True)


def mark_run_buttons():
    components.html(
        '<script>(function(){'
        'try{var doc=window.parent.document;}catch(e){return;}'
        'function tagAll(){'
        'var bs=doc.querySelectorAll("[data-testid=\\"stButton\\"] button");'
        'for(var i=0;i<bs.length;i++){'
        'var t=(bs[i].textContent||"").trim();'
        'if(t.charAt(0)==="▶"){bs[i].classList.add("rx-run-btn");}'
        'else{bs[i].classList.remove("rx-run-btn");}'
        '}'
        '}'
        'tagAll();'
        'var mo=new MutationObserver(function(){tagAll();});'
        'mo.observe(doc.body,{childList:true,subtree:true});'
        '})();</script>', height=0)


def rx_info(text):
    safe = str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    st.markdown('<div class="rxlib-info">%s</div>' % safe, unsafe_allow_html=True)

def rx_error(text):
    safe = str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    st.markdown('<div class="rxlib-error">%s</div>' % safe, unsafe_allow_html=True)


def brand_header():
    safe = str(ls.DEFAULT_TITLE).replace('\\', '\\\\').replace('"', '\\"').replace('\n', ' ')
    st.markdown(
        '<style>'
        '[data-testid="stSidebarHeader"]::before{'
        'content:"%s";'
        'font-size:1.2rem;font-weight:700;'
        'color:#2563EB;opacity:.92;'
        'padding-left:.35rem;min-width:0;'
        'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'
        'align-self:center;'
        '}'
        '</style>' % safe, unsafe_allow_html=True)


def sidebar_controls():
    with st.sidebar:
        st.subheader('数据源')
        source = st.radio('数据提供层', ['mysql', 'api', 'import'], index=1,
                          format_func=lambda x: {'mysql': '直连 MySQL',
                                                 'api': 'API',
                                                 'import': '本地 CSV 导入'}[x],
                          horizontal=True,
                          help='选择回测和基准数据的访问方式；"本地 CSV 导入"'
                               '从 config 指定的目录按月读取 daily_YYYYMM.csv。')
        if source == 'api':
            _api = platform_env.get_api_config()
            if not (_api.get('base_url') and _api.get('token')):
                st.caption('请在「系统设置」中填写 API 地址和访问令牌。')
        elif source == 'import':
            _import_dir = platform_env.get_import_config().get('data_dir')
            if not _import_dir:
                st.caption('请在「系统设置」中配置导入数据目录。')
            else:
                st.caption('导入目录：%s' % _import_dir)

        st.subheader('股票池 / 区间')
        pool, pool_spec = pool_controls()
        c1, c2 = st.columns(2)
        start = c1.date_input('开始日期', value=dt.date(2026, 3, 1),
                              help='回测起始交易日，窗口会自动向前取约 2 个月数据用于指标预热。')
        try:
            default_end = latest_trade_date(source, ls.get_db_config())
        except Exception:
            default_end = dt.date(2026, 9, 18)
        end = c2.date_input('结束日期', value=default_end,
                            help='回测结束交易日，默认自动取库内最新交易日。')
        procs = st.number_input('并行进程数', min_value=1, max_value=32,
                                value=max(1, (os.cpu_count() or 6) - 2),
                                help='构建面板时的 CPU 进程数；机器核多可调大以加快首次建库。')

        tag = build_tag(pool, pool_spec, start, end)

        st.subheader('预设一键载入')
        preset = st.selectbox('预设策略', ['（不载入，用下方自定义）'] +
                              sorted(fl.preset_cfgs().keys()),
                              format_func=lambda k: k if k.startswith('（') else _preset_label(k),
                              help='选择后把对应参数填入下方各控件，可再手动微调。')
        st.caption('所选预设仅为参数模板，不构成投资建议；历史回测不代表未来收益。')
        base = fl.base_cfg()
        if preset != '（不载入，用下方自定义）':
            base = fl.preset_cfgs()[preset]

        cfg = strategy_controls(base)

        st.subheader('其它')
        fee_bps = st.number_input('单边费率（bp，万分之3=3）', min_value=0.0,
                                  max_value=100.0, value=0.0, step=0.5,
                                  help='买入和卖出各收取一次的交易费率，单位为基点（1bp=0.01%）。默认0表示不计费。')

        st.divider()
        st.button('退出登录', on_click=_do_logout, use_container_width=True)

    st.session_state.update(
        user_id=pool_spec.get('user_id'), start=pd.Timestamp(start),
        end=pd.Timestamp(end), procs=int(procs), source=source, cfg=cfg,
        fee_bps=float(fee_bps), pool_label=pool, pool_spec=pool_spec, tag=tag)


def pool_controls():
    choices = ['科创50（测试）']
    for w in ls.list_watchlists():
        choices.append('自定义 · %s' % w)
    for b in fl.BOARD_ORDER:
        choices.append('板块 · %s' % b)
    choices.append('全 A（全市场）')
    pool = st.selectbox('股票池', choices, index=0,
                        help='选择回测/选股的标的范围；默认为科创50（测试）股票池，自定义池在"自选池管理"页维护。')

    pool_spec = {'user_id': None, 'boards': None, 'exclude_st': False,
                 'exclude_new_days': None, 'custom_codes': None}
    if pool.startswith('科创50'):
        pool_spec['custom_codes'] = fl.test_pool_rxcodes()
    elif pool.startswith('板块 · '):
        pool_spec['boards'] = [pool.split('·', 1)[1].strip()]
    elif pool.startswith('自定义 · '):
        name = pool.split('·', 1)[1].strip()
        wl = ls.load_watchlist(name)
        pool_spec['custom_codes'] = wl['rxcode'].tolist()

    exclude_st = st.checkbox('排除 ST 股', value=False,
                             help='在所选股票池基础上剔除所有 ST/*ST 风险警示股票。')
    exclude_new = st.checkbox('排除次新股', value=False,
                              help='剔除上市未满下方天数的股票。')
    new_days = st.number_input('次新股上市天数门槛', min_value=1, max_value=1000,
                               value=fl.NEW_LISTING_DAYS, disabled=not exclude_new,
                               help='上市自然日数小于该值的股票会被剔除，默认90天。')
    pool_spec['exclude_st'] = exclude_st
    pool_spec['exclude_new_days'] = int(new_days) if exclude_new else None
    return pool, pool_spec


def build_tag(pool, pool_spec, start, end):
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    if pool_spec.get('custom_codes') is not None:
        codes = pool_spec['custom_codes']
        import zlib
        key = 'wl%x' % (zlib.crc32(','.join(sorted(codes)).encode('utf-8')) & 0xffffffff)
    elif pool_spec.get('boards'):
        key = 'b' + pool_spec['boards'][0][:2]
    else:
        key = 'mkt'
    sfx = ''
    if pool_spec.get('exclude_st'):
        sfx += 's'
    if pool_spec.get('exclude_new_days'):
        sfx += 'n%d' % pool_spec['exclude_new_days']
    return '%s_%s_%s%s' % (key, s.strftime('%m%d'), e.strftime('%m%d'), sfx)


def strategy_controls(base):
    """侧边栏 Lance B 支撑与阻力五要点参数控件：入场管线与仓位 / 五要点门槛 / 平台出场适配口径。

    参数多（30+），用折叠 expander 分组 + 每个控件一行中文 help，避免侧边栏臃肿；
    顶部提供预设一键载入与双管道开关；最后用 fl.base_cfg(**...) 组装返回值。
    """
    cfg = dict(base)
    _pipe_opts = ['breakout', 'both', 'reversal']
    _pipe_names = {'breakout': 'A盘整突破', 'both': 'A+B 双管道',
                   'reversal': 'B情绪反转'}

    with st.expander('入场管线与仓位', expanded=True):
        _cur_pipe = (cfg.get('pipeline') or 'both') if \
            (cfg.get('pipeline') or 'both') in _pipe_opts else 'both'
        pipeline = st.pills(
            '入场管线', _pipe_opts,
            default=_cur_pipe, format_func=_pipe_names.get, selection_mode='single',
            help='A盘整突破 / B情绪反转 / both 双管道并行（同标的不同日不可重复开仓）。')
        if not pipeline:
            pipeline = _cur_pipe
        topn = st.number_input('持仓槽位 topn', 1, 50, int(cfg['topn']),
                               help='同时持有的标的上限；活跃口径横截面按该数量取前 topn。')
        max_weight = st.slider(
            '单票权重上限 %', 0.05, 0.50, float(cfg['max_weight']), 0.05,
            help='归一化后单只标的实际持仓权重不超过该上限；当日各标的总和不超过 100%。')
        activity_th = st.slider(
            '活跃门槛 activity_th', 1.0, 5.0, float(cfg['activity_th']), 0.1,
            help='activity_raw 达到该值才视为活跃；是管道A 内/横截面排序的准入门槛。')
        allow_bear = st.checkbox(
            '空头共振也可做多', value=bool(cfg['allow_bear']),
            help='开启后允许微弱负共振（resonance 略小于 0）标的也纳入多头候选；默认关闭。')
        exit_enabled = st.checkbox(
            '平台出场链总开关', value=bool(cfg['exit_enabled']),
            help='关闭后下方全部平台出场规则失效，仅按到期/末日估值处理。'
                 'Breitstein 原文未定义出场，此为平台适配口径。')

    with st.expander('要点条件开关（可选）', expanded=False):
        st.caption('默认全部勾选=五要点全 AND；取消勾选某项，则该条件不再作为选股拦截。'
                   '属于该管道但未在此列出的条件（如下方阈值）仍按数值门槛生效。')
        c1, c2 = st.columns(2)
        with c1:
            use_activity = st.checkbox(
                '要点一 活跃度', value=bool(cfg['use_activity']),
                help='要求 activity_raw≥activity_th（两管道）。')
            use_clean_level = st.checkbox(
                '要点二 干净价位', value=bool(cfg['use_clean_level']),
                help='要求干净阻力/支撑位置信度>conf_th（两管道）。')
            use_breakout = st.checkbox(
                '要点二 突破箱顶', value=bool(cfg['use_breakout']),
                help='管道A：要求当日收盘有效突破箱顶。')
            use_compression = st.checkbox(
                '要点三 盘整压缩', value=bool(cfg['use_compression']),
                help='管道A：要求突破前处于盘整压缩态。')
        with c2:
            use_roc = st.checkbox(
                '要点二 突破速度ROC', value=bool(cfg['use_roc']),
                help='管道A：要求突破斜率 ROC>roc_min。')
            use_emotion = st.checkbox(
                '要点四 极端情绪', value=bool(cfg['use_emotion']),
                help='管道B：要求 |emotion_score|≥情绪门槛。')
            use_reversal_entry = st.checkbox(
                '要点四 反转入场确认', value=bool(cfg['use_reversal_entry']),
                help='管道B：要求出现投降+收复AVWAP 的入场信号。')
            use_compression_b_pre = st.checkbox(
                '要点四 追高拦截·盘整前置',
                value=bool(cfg.get('use_compression_b_pre', False)),
                help='管道B：要求入场前一根已盘整压缩（未盘整的放量追高形态被拦截）。')
            use_resonance = st.checkbox(
                '要点五 多周期共振', value=bool(cfg['use_resonance']),
                help='要求多头共振：默认仅 resonance>=0 放行做多（即"空头共振也可做多"关闭时不进场）。'
                     '取消勾选后不再因共振方向拒绝入场，仓位仍按 position_mult 加权。')

    with st.expander('要点一：活跃度', expanded=False):
        atr_ratio_th = st.slider(
            'ATR 比率门槛 atr_ratio_th', 1.0, 4.0,
            float(cfg['atr_ratio_th']), 0.1,
            help='ATR / ATR中位数 > 该比值视为放量活跃，默认 2.0。')
        vol_ratio_th = st.slider(
            '量比门槛 vol_ratio_th', 1.0, 5.0,
            float(cfg['vol_ratio_th']), 0.1,
            help='Volume_Ratio ＞ 该值视为放量活跃，默认 1.8。')
        gap_th = st.slider(
            '跳空门槛 gap_th %', 0.5, 5.0, float(cfg['gap_th']) * 100, 0.5,
            help='单日跳空幅度百分比超过该值视为跳空活跃，默认 2%。')
        atr_window = st.number_input('ATR 主窗口 atr_window', 5, 30,
                                     int(cfg['atr_window']),
                                     help='活跃度层 ATR 计算窗口，默认 14。')
        vol_sma_window = st.number_input('量能基准窗口 vol_sma_window', 5, 60,
                                         int(cfg['vol_sma_window']),
                                         help='Volume_Ratio 分母的 SMA 窗口，默认 20。')

    with st.expander('要点二：干净价位', expanded=False):
        pivot_order = st.number_input('枢轴点 order', 3, 8,
                                      int(cfg['pivot_order']),
                                      help='识别阻力/支撑枢轴的左右确认根数，默认 5。')
        conf_th = st.slider('价位置信度门槛 conf_th', 0.0, 0.6,
                            float(cfg['conf_th']), 0.01,
                            help='阻力/支撑位被反复触发的置信度门槛，默认 0.10。'
                                 '低于该值的“干净价位”不参与触发。')
        roc_n = st.number_input('突破速度 ROC 窗口', 1, 10, int(cfg['roc_n']),
                                help='管道A 突破速度使用的涨速窗口，默认 3。')
        roc_min = st.slider('突破速度门槛 roc_min %', 0.1, 5.0,
                            float(cfg['roc_min']) * 100, 0.1,
                            help='roc_n 窗口内累计涨幅百分比需超过该值的管道A 门槛，默认 1%。')
        conf_window = st.number_input('价位置信度窗口', 20, 250,
                                      int(cfg['conf_window']),
                                      help='计算阻力/支撑稳定性的统计窗口，默认 100。')
        touch_tol = st.slider('触及容差 touch_tol %', 0.1, 1.0,
                              float(cfg['touch_tol']) * 100, 0.05,
                              help='价格距关键位在该百分比内视为“触及”，默认 0.2%。')

    with st.expander('要点三：盘整', expanded=False):
        comp_window = st.number_input('压缩盒窗口 comp_window', 5, 30,
                                      int(cfg['comp_window']),
                                      help='区间压缩识别的 N 根窗口，默认 10。')
        pct_th = st.slider('压缩百分位门槛 pct_th %', 2.0, 30.0,
                           float(cfg['pct_th']) * 100, 1.0,
                           help='box 在历史区间压缩百分位超过该值视为压缩，默认 10%。')
        z_th = st.number_input('压缩 z 门槛 z_th', -2.5, -0.5,
                               float(cfg['z_th']), 0.1,
                               help='box 宽度相对历史分布的 z 分需低于该值（越紧负向），默认 −1.0。')
        comp_b_pct_th = st.slider(
            '追高拦截·盘整前置门槛 comp_b_pct_th %', 5.0, 30.0,
            float(cfg.get('comp_b_pct_th', 0.12)) * 100, 1.0,
            help='管道B 追高拦截：入场前一根 RANGE_PCT 需低于该值（或 RANGE_Z<z_th）'
                 '才允许入场，默认 12%。')
        drift_th = st.slider('净漂移门槛 drift_th', 0.1, 0.5,
                             float(cfg['drift_th']), 0.05,
                             help='减去趋势后箱体中心净漂移需低于该值才视为“横向”，默认 0.3。')
        slope_th = st.slider('斜率门槛 slope_th %', 0.05, 0.30,
                             float(cfg['slope_th']) * 100, 0.01,
                             help='箱体线性拟合斜率 vs 均价的临界值，默认 0.1%。')
        cv_th = st.slider('变异系数门槛 cv_th', 0.2, 0.8,
                          float(cfg['cv_th']), 0.05,
                          help='箱体内走势变异系数低于该值视为紧凑，默认 0.5。')
        cv_day_ratio = st.slider('单日区间占比 cv_day_ratio', 0.2, 0.8,
                                 float(cfg['cv_day_ratio']), 0.05,
                                 help='任一单日振幅不超过箱体宽度的该比例，默认 0.5。')
        hist_window = st.number_input(
            '压缩历史窗口 hist_window', 60, 252, int(cfg['hist_window']),
            help='压缩百分位的比较历史长度；上市较短的标的自动回退 120/60 窗口。')

    with st.expander('要点四：情绪反应', expanded=False):
        bb_window = st.number_input('布林带窗口 bb_window', 5, 40,
                                    int(cfg['bb_window']),
                                    help='情绪层布林带计算窗口，默认 20。')
        bb_z_th = st.selectbox(
            '布林带 z 门槛 bb_z_th', [1.0, 1.5, 2.0],
            index=[1.0, 1.5, 2.0].index(float(cfg['bb_z_th'])),
            help='2.0=原文口径≈4σ，触发罕见；1.5/1.0 为放宽口径可配。')
        sentiment_th = st.slider('情绪评分门槛 sentiment_th', 1.0, 5.0,
                                 float(cfg['sentiment_th']), 0.1,
                                 help='情绪剖面的 |sentiment_score| 极值门槛，默认 2.5。')
        pipeline_b_emotion_th = st.slider(
            '管道B 情绪门槛', 1.0, 4.0, float(cfg['pipeline_b_emotion_th']), 0.1,
            help='管道B（投降+收复AVWAP）要求当日情绪极值超过该值，默认 2.0。')
        capitulate_vol_mult = st.slider(
            '投降量倍数 capitulate_vol_mult', 1.0, 4.0,
            float(cfg['capitulate_vol_mult']), 0.1,
            help='投降 K 线成交量需超过量基准的该倍数，默认 2.0。')
        emotion_extreme_th = st.slider(
            '情绪极值观察区间 emotion_extreme_th', 1.5, 6.0,
            float(cfg['emotion_extreme_th']), 0.1,
            help='识别情绪极值（供投降/收复判断）的幅度门槛，默认 2.5。')
        cons_down_min = st.number_input('连续阴线下限 cons_down_min', 1, 10,
                                        int(cfg['cons_down_min']),
                                        help='识别连续下跌所需的最少阴线根数，默认 3。')
        cons_down_max = st.number_input('连续阴线上限 cons_down_max', 1, 10,
                                        int(cfg['cons_down_max']),
                                        help='识别连续下跌的最大阴线根数，默认 5；若低于下限自动取 max=min。')
        if cons_down_max < cons_down_min:
            cons_down_max = cons_down_min
        avwap_anchor = st.selectbox(
            'AVWAP 锚点 avwap_anchor', ['open', 'prev_close'],
            index=['open', 'prev_close'].index(cfg['avwap_anchor']),
            help='投降后收复的基准：open＝投降 K 线当日开盘价（默认），'
                 'prev_close＝其前一日收盘价。')

    with st.expander('要点五：多周期共振与出场', expanded=False):
        ma_fast = st.number_input('快 MA ma_fast', 3, 100, int(cfg['ma_fast']),
                                  help='趋势快均线，用于共振方向判定；短历史自动回退 MA%d。'
                                       % int(cfg['ma_fast_degrade']))
        ma_slow = st.number_input('慢 MA ma_slow', 10, 250, int(cfg['ma_slow']),
                                  help='趋势慢均线；短历史自动回退 MA%d。'
                                       % int(cfg['ma_slow_degrade']))
        ma_fast_degrade = st.number_input('快 MA 短史回退', 2, 30,
                                          int(cfg['ma_fast_degrade']),
                                          help='历史过短时快 MA 回退到该窗口，默认 3。')
        ma_slow_degrade = st.number_input('慢 MA 短史回退', 3, 60,
                                          int(cfg['ma_slow_degrade']),
                                          help='历史过短时慢 MA 回退到该窗口，默认 5。')
        res_overlap_tol = st.slider(
            '关键位重合容差 res_overlap_tol %', 0.1, 2.0,
            float(cfg['res_overlap_tol']) * 100, 0.1,
            help='日/周线关键位重合距离小于该百分比视为共振，默认 0.5%。')
        daily_res_window = st.number_input(
            '日线阻力窗口 daily_res_window', 5, 60,
            int(cfg['daily_res_window']),
            help='日线关键阻力 roll 窗口，默认 20。')
        weekly_res_window = st.number_input(
            '周线阻力窗口 weekly_res_window', 1, 12,
            int(cfg['weekly_res_window']),
            help='周线关键阻力 roll 窗口，默认 4。')
        st.caption('—— 出场为平台适配口径，Breitstein 原文未定义，'
                   '可整体关闭（见顶部 exit_enabled）——')
        stop_loss = st.slider(
            '固定止损 stop_loss %（0=关）', 0, 20,
            int(round((cfg['stop_loss'] or 0.0) * 100)), 1,
            help='按买入价固定百分比止损；0 表示关闭。# 该出场为平台适配口径。')
        atr_exit = st.checkbox('ATR 止损 atr_exit', value=bool(cfg['atr_exit']),
                               help='按买入价 − 倍数×进场根 ATR 止损；平台适配口径。')
        atr_mult = st.number_input('ATR 止损倍数 atr_mult', 0.5, 10.0,
                                   float(cfg['atr_mult']), 0.5,
                                   disabled=not atr_exit)
        avwap_stop_buf = st.slider(
            '结构/AVWAP 止损缓冲 %', 0.5, 5.0,
            float(cfg['avwap_stop_buf']) * 100, 0.1,
            help='结构锚定价（含 AVWAP）下方的缓冲百分比，默认 2%。')
        cons_weak_exit = st.checkbox(
            '共振走弱出场 cons_weak_exit', value=bool(cfg['cons_weak_exit']),
            help='持仓期间共振回落低于阈值、或转负时离场；平台适配口径，默认开。')
        cons_weak_th = st.number_input('共振走弱阈值 cons_weak_th', -6, 0,
                                       int(cfg['cons_weak_th']),
                                       disabled=not cons_weak_exit,
                                       help='resonance 持续低于该值视为共振走弱，默认 −2。')
        activity_decay_exit = st.checkbox(
            '活跃衰减出场 activity_decay_exit',
            value=bool(cfg['activity_decay_exit']),
            help='活跃分连续 N 日衰减则提前离场（默认关）。')
        activity_decay_days = st.number_input(
            '连续衰减天数', 1, 60, int(cfg['activity_decay_days']),
            disabled=not activity_decay_exit,
            help='活跃连续衰减多少日触发离场，默认 3。')
        max_hold = st.number_input('最长持有自然日 max_hold（0=不限）', 0, 500,
                                   int(cfg['max_hold'] or 0),
                                   help='从开仓日起最多持有多少自然日；0 表示不限。')
        trail_exit = st.checkbox(
            '移动止盈/回撤出场 trail_exit',
            value=bool(cfg.get('trail_exit', False)),
            help='浮盈达标后，收盘自持仓期最高价回撤≥trail_pct 即离场（解决"持有过久、'
                 '高位回吐才被清"）。默认关。')
        trail_pct = st.number_input('移动止盈回撤阈值 % trail_pct', 1.0, 40.0,
                                    float(cfg.get('trail_pct', 0.10)) * 100, 1.0,
                                    disabled=not trail_exit,
                                    help='自持仓期最高价的回撤百分比，默认 10%。')
        trail_min_gain = st.number_input(
            '触发最低浮盈 % trail_min_gain', 0.0, 50.0,
            float(cfg.get('trail_min_gain', 0.03)) * 100, 1.0,
            disabled=not trail_exit,
            help='持仓浮盈至少达到该百分比才启用移动止盈，默认 3%。')
        trend_exit = st.checkbox(
            '趋势/AVWAP 破坏出场 trend_exit',
            value=bool(cfg.get('trend_exit', False)),
            help='收盘跌破 AVWAP（带缓冲）即离场。默认关。')
        trend_buf = st.number_input('跌破 AVWAP 缓冲 % trend_buf', 0.0, 10.0,
                                    float(cfg.get('trend_buf', 0.0)) * 100, 0.5,
                                    disabled=not trend_exit,
                                    help='收盘低于 AVWAP 百分之几才视为趋势破坏，默认 0%。')

    return fl.base_cfg(
        pipeline=pipeline, topn=int(topn), max_weight=float(max_weight),
        activity_th=float(activity_th), allow_bear=bool(allow_bear),
        exit_enabled=bool(exit_enabled),
        atr_ratio_th=float(atr_ratio_th), vol_ratio_th=float(vol_ratio_th),
        gap_th=float(gap_th) / 100.0, atr_window=int(atr_window),
        vol_sma_window=int(vol_sma_window),
        pivot_order=int(pivot_order), conf_th=float(conf_th), roc_n=int(roc_n),
        roc_min=float(roc_min) / 100.0, conf_window=int(conf_window),
        touch_tol=float(touch_tol) / 100.0,
        comp_window=int(comp_window), pct_th=float(pct_th) / 100.0,
        z_th=float(z_th), drift_th=float(drift_th),
        slope_th=float(slope_th) / 100.0, cv_th=float(cv_th),
        cv_day_ratio=float(cv_day_ratio), hist_window=int(hist_window),
        bb_window=int(bb_window), bb_z_th=float(bb_z_th),
        sentiment_th=float(sentiment_th),
        pipeline_b_emotion_th=float(pipeline_b_emotion_th),
        capitulate_vol_mult=float(capitulate_vol_mult),
        emotion_extreme_th=float(emotion_extreme_th),
        cons_down_min=int(cons_down_min), cons_down_max=int(cons_down_max),
        avwap_anchor=avwap_anchor,
        ma_fast=int(ma_fast), ma_slow=int(ma_slow),
        ma_fast_degrade=int(ma_fast_degrade),
        ma_slow_degrade=int(ma_slow_degrade),
        res_overlap_tol=float(res_overlap_tol) / 100.0,
        daily_res_window=int(daily_res_window),
        weekly_res_window=int(weekly_res_window),
        stop_loss=(float(stop_loss) / 100.0 if stop_loss > 0 else None),
        atr_exit=bool(atr_exit), atr_mult=float(atr_mult),
        avwap_stop_buf=float(avwap_stop_buf) / 100.0,
        cons_weak_exit=bool(cons_weak_exit), cons_weak_th=int(cons_weak_th),
        activity_decay_exit=bool(activity_decay_exit),
        activity_decay_days=int(activity_decay_days),
        use_activity=bool(use_activity), use_clean_level=bool(use_clean_level),
        use_breakout=bool(use_breakout), use_compression=bool(use_compression),
        use_roc=bool(use_roc), use_emotion=bool(use_emotion),
        use_reversal_entry=bool(use_reversal_entry),
        use_resonance=bool(use_resonance),
        use_compression_b_pre=bool(use_compression_b_pre),
        comp_b_pct_th=float(comp_b_pct_th) / 100.0,
        max_hold=(int(max_hold) if max_hold > 0 else None),
        trail_exit=bool(trail_exit),
        trail_pct=float(trail_pct) / 100.0,
        trail_min_gain=float(trail_min_gain) / 100.0,
        trend_exit=bool(trend_exit),
        trend_buf=float(trend_buf) / 100.0)


def mode_radio(label, values, current, names, help=None):
    return st.radio(label, values, index=values.index(current),
                    format_func=dict(zip(values, names)).get, horizontal=True,
                    help=help)


def pct_input(label, val, help=None):
    v = st.number_input('%s（%%，0=关闭）' % label, min_value=0.0, max_value=100.0,
                        value=round(float(val) * 100, 1) if val is not None else 0.0,
                        step=0.5, help=help)
    return (v / 100.0) if v > 0 else None


# -------------------- 可溯源：参数快照 --------------------

def snapshot(cfg, tag):
    s = st.session_state
    snap_id = int(s.get('snap_seq', 0)) + 1
    s['snap_seq'] = snap_id
    snap = {'run_time': dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'pool': s.pool_label, 'user_id': s.user_id,
            'start': str(s.start.date()), 'end': str(s.end.date()),
            'source': s.source, 'fee_bps': s.fee_bps, 'cache_tag': tag,
            'snap_id': snap_id, 'cfg': cfg}
    st.session_state.setdefault('history', []).append(snap)
    return snap


def show_snapshot(snap):
    with st.expander('本次运行参数快照（可溯源）'):
        st.json(snap)
        st.download_button('下载参数快照 JSON',
                           data=json.dumps(snap, ensure_ascii=False, indent=2,
                                           default=str),
                           file_name='factor_lab_snapshot_%s.json'
                           % dt.datetime.now().strftime('%Y%m%d_%H%M%S'),
                           key='snap_dl_%s' % snap.get('snap_id', 0))


def run_signature():
    s = st.session_state
    return json.dumps(
        [str(s.tag), str(s.start.date()), str(s.end.date()),
         s.user_id, s.source, s.fee_bps, s.cfg,
         str(s.get('intraday_tag'))],
        ensure_ascii=False, default=str, sort_keys=True)


# -------------------- Tab1 历史回测 --------------------

def tab_single(state):
    cal, panels, provider, tag = state
    st.button('▶ 运行历史回测', type='primary', key='run_single')
    s = st.session_state
    if s.get('run_single'):
        with st.spinner('回测计算中...'):
            res = fl.backtest_once(
                s.cfg, s.start, s.end, user_id=s.user_id,
                fee_bps=s.fee_bps, provider=provider,
                panels_state=(cal, panels, tag))
        s.last_single = res
        s.last_single_tag = tag
        s.last_single_sig = run_signature()
        s.last_single_snap = snapshot(res['cfg'], tag)
        s.last_single_intraday = s.get('intraday_info')
    res = s.get('last_single')
    if res is None:
        rx_info('在左侧设置参数后点击运行。')
        return
    tag = s.last_single_tag
    if s.get('last_single_sig') != run_signature():
        rx_info('左侧参数已修改，当前为上次运行结果；点击运行以更新。')
    if s.get('last_single_intraday'):
        _ii = s['last_single_intraday']
        st.caption('盘中口径：快照 %s %s，覆盖 %d 只（结果随快照刷新而变）。'
                   % (_ii['trade_date'], _ii['tick_time'] or '-',
                      _ii['n_snapshot']))
    if res.get('bench_degraded'):
        st.warning(res['bench_degraded'])
    m = res['metrics']

    c = st.columns(6)
    c[0].metric('总收益', '%.2f%%' % m['final'])
    c[1].metric('年化收益', '%.2f%%' % m['ann_return'])
    c[2].metric('年化波动', '%.2f%%' % m['ann_vol'])
    c[3].metric('最大回撤', '%.2f%%' % m['mdd'])
    c[4].metric('夏普比率 Sharpe', fmt(m['sharpe']))
    c[5].metric('卡玛比率 Calmar', fmt(m['calmar']))
    c = st.columns(6)
    c[0].metric('Alpha', '%.2f%%' % m['alpha'])
    c[1].metric('Beta', fmt(m['beta']))
    c[2].metric('信息比率', fmt(m['info_ratio']))
    c[3].metric('胜率', '%.1f%%' % m['win_rate'] if m['n_trades'] else '-')
    c[4].metric('盈亏比', fmt(m['pf']))
    c[5].metric('开平 / 未结 / 计划数',
                '%s / %s / %s' % (m['n_trades'], m.get('n_open', 0),
                                  m.get('n_entries', 0)))

    eq = res['equity']
    if eq is not None and len(eq):
        import plotly.graph_objects as go
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eq['date'], y=eq['equity'], name='策略净值'))
        if 'bench_equity' in eq.columns:
            fig.add_trace(go.Scatter(x=eq['date'], y=eq['bench_equity'],
                                     name='沪深300', line=dict(dash='dash')))
        fig.update_layout(height=420, hovermode='x unified', yaxis_title='净值')
        st.plotly_chart(fig, use_container_width=True)

        dd = eq['equity'] / eq['equity'].cummax() - 1.0
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=eq['date'], y=dd * 100, name='回撤%',
                                  fill='tozeroy', line=dict(color='#2563EB')))
        fig2.update_layout(height=240, yaxis_title='回撤%')
        st.plotly_chart(fig2, use_container_width=True)

    tr = res['trades']
    st.markdown('**持仓交易明细**')
    st.caption('单计划单均价；`exit_reason=open` 为区间结束仍未平仓持仓，按末日收盘估值。'
               '`entry_event` 为开仓事件：breakout(A盘整突破)/reversal(B投降+收复AVWAP)；'
               '`weight` 为归一化后的实际权重(≤单票上限、当日总和≤100%)；'
               '`resonance_at_entry` 为开仓日共振分。')
    st.caption('量化指标辅助判断、不替代自主裁量；阈值需回测校准，出场与A股约束为平台适配口径。')
    if len(tr):
        tr = tr.reset_index(drop=True)
        if 'ret' in tr.columns:
            tr = tr.rename(columns={'ret': 'ret(%)'})
            tr['ret(%)'] = (tr['ret(%)'] * 100.0).round(2)
        tr.index = tr.index + 1
        tr.index.name = '序号'
    st.dataframe(tr, use_container_width=True, height=320)
    _td1, _td2 = st.columns([1, 1], gap="small")
    with _td1:
        st.download_button('下载明细（UTF-8）', data=tr.to_csv(
            index=bool(len(tr)), encoding='utf-8-sig'),
                           file_name='factor_lab_trades_%s.csv' % tag,
                           use_container_width=True)
    with _td2:
        st.download_button('下载明细（GBK）',
                           data=tr.to_csv(index=bool(len(tr))).encode(
                               'gb18030', errors='replace'),
                           file_name='factor_lab_trades_%s_gbk.csv' % tag,
                           use_container_width=True,
                           help='若 UTF-8 版本打开中文乱码，请下载此版本。')
    show_snapshot(s.last_single_snap)


def fmt(x):
    return '-' if x is None or pd.isna(x) else '%.2f' % x


def strip_code(s):
    return str(s).split('.')[0]


def show_df(df, code_cols=()):
    if df is None or not len(df):
        return df
    out = df.copy()
    for col in code_cols:
        if col in out.columns:
            out[col] = out[col].map(strip_code)
    return out


def to_csv(df):
    return df.to_csv(index=False, encoding='utf-8-sig')


def to_csv_gbk(df):
    return df.to_csv(index=False).encode('gb18030', errors='replace')


# -------------------- Tab2 批量网格 --------------------

def tab_grid(state):
    cal, panels, provider, tag = state
    kind = st.radio('批量类型', ['presets', 'grid（24组）'], horizontal=True,
                    format_func=lambda v: '具名预设 presets' if v == 'presets' else v,
                    help='presets：4 个 Breitstein 具名预设；grid：活跃门槛×压缩×情绪门槛×管道偏置 共 24 组。')
    s = st.session_state
    if st.button('▶ 运行批量', type='primary'):
        mask_cache = {}
        start, end = s.start, s.end
        ncal = [pd.Timestamp(x) for x in cal if start <= pd.Timestamp(x) <= end]
        cut = int(len(ncal) * 0.65)
        tr_s, tr_e = ncal[0], ncal[cut - 1]
        oo_s, oo_e = ncal[cut], ncal[-1]

        if kind == 'presets':
            cfgs = fl.preset_cfgs()
        else:
            cfgs = {'grid_%02d' % i: c for i, c in enumerate(fl.bt_grid(), 1)}

        rows, details = [], {}
        bar = st.progress(0.0, text='批量回测中...')
        for i, (cid, cfg) in enumerate(cfgs.items(), 1):
            row, det = fl.evaluate(cfg, ncal, panels, mask_cache,
                                   tr_s, tr_e, oo_s, oo_e, s.fee_bps)
            row['cid'] = cid
            rows.append(row)
            details[cid] = det
            bar.progress(i / len(cfgs), text='%s (%d/%d)' % (cid, i, len(cfgs)))
        bar.empty()
        df = pd.DataFrame(rows).sort_values(
            ['oos_final', 'oos_mdd'], ascending=[False, True]).reset_index(drop=True)
        s.grid_df = df
        s.grid_details = details
        s.grid_kind = kind
        s.grid_tag = tag
        s.grid_sig = json.dumps([run_signature(), kind], ensure_ascii=False)
    df = s.get('grid_df')
    if df is None:
        rx_info('选择批量类型后点击运行。')
        return
    details = s.grid_details
    kind = s.grid_kind
    tag = s.grid_tag
    if s.get('grid_sig') != json.dumps([run_signature(), kind],
                                       ensure_ascii=False):
        rx_info('左侧参数或批量类型已修改，当前为上次运行结果；点击运行以更新。')
    st.dataframe(df, use_container_width=True, height=420)
    st.download_button('下载排名 CSV', data=to_csv(df),
                       file_name='factor_lab_%s_%s.csv'
                       % ('presets' if kind == 'presets' else 'grid', tag))

    import plotly.graph_objects as go
    sel = st.multiselect('叠加净值曲线', list(details.keys()),
                         default=df['cid'].head(3).tolist())
    if sel:
        fig = go.Figure()
        for cid in sel:
            eq = details[cid]['equity']
            if len(eq):
                fig.add_trace(go.Scatter(x=eq['date'], y=eq['equity'], name=cid))
        fig.update_layout(height=420, hovermode='x unified', yaxis_title='净值')
        st.plotly_chart(fig, use_container_width=True)


def tab_picks(state):
    cal, panels, provider, tag = state
    s = st.session_state
    is_api = provider.name == 'api'
    meta_info = api_meta() if is_api else {}
    allow_intraday = (not is_api) or bool(meta_info.get('allow_intraday', False))
    latest_close = pd.Timestamp(cal[-1]).date()
    intraday_info = None
    if allow_intraday:
        probe = intraday_probe(s.source, ls.get_db_config())
        if probe is not None and probe['trade_date'] > latest_close:
            intraday_info = probe
    dates = [pd.Timestamp(x).date() for x in cal]
    max_d = dates[-1]
    if intraday_info is not None and intraday_info['trade_date'] > max_d:
        max_d = intraday_info['trade_date']
    d = st.date_input('选择交易日', value=dates[-1],
                      min_value=dates[0], max_value=max_d)
    is_intraday_day = (intraday_info is not None
                       and d == intraday_info['trade_date'])
    if intraday_info is not None:
        st.caption('检测到盘中快照（%s，覆盖 %d 只），可选择该日执行盘中选股。'
                   % (intraday_info['trade_date'].strftime('%Y-%m-%d'),
                      intraday_info['n_snapshot']))

    mode_names = {
        'active': '活跃TopN',
        'breakout': '盘整突破',
        'reversal': '投降+收复AVWAP',
        'resonance': '共振机会',
    }
    _keys = list(fl.PICK_MODES.keys())
    _mcols = st.columns(len(_keys))
    mode_sel = {k: _mcols[j].checkbox(mode_names[k], value=True)
                for j, k in enumerate(_keys)}
    modes = [k for k in _keys if mode_sel.get(k)]
    st.caption('可勾选一个或多个候选口径，将分别查询并合并汇总。')
    require_major = st.checkbox('仅标记共振可交易（resonance>0）标的',
                                value=False,
                                help='只把开仓日共振 resonance>0、且满足 tradable 基础条件的标的标记为可交易。')

    if st.button('▶ 查询名单', type='primary'):
        if not modes:
            rx_info('请至少勾选一个候选口径。')
            return
        _mode_txt = '、'.join(mode_names.get(m, m) for m in modes)
        spinner_txt = ('正在重算盘中快照并查询 %s 名单...' % _mode_txt
                       if is_intraday_day else '正在查询 %s 名单...' % _mode_txt)
        with st.spinner(spinner_txt):
            ical = ipanels = iinfo = None
            if is_intraday_day:
                try:
                    ical, ipanels, iinfo = fl.load_or_build_intraday(
                        tag, s.user_id, s.start, s.procs,
                        read_config=ls.get_db_config(),
                        boards=s.pool_spec.get('boards'),
                        exclude_st=s.pool_spec.get('exclude_st', False),
                        exclude_new_days=s.pool_spec.get('exclude_new_days'),
                        custom_codes=s.pool_spec.get('custom_codes'),
                        panel_version=ls.get_panel_version(),
                        provider=provider, day=pd.Timestamp(d))
                except Exception as e:
                    st.error('盘中选股失败：%s' % e)
                    return
            pieces, n = [], 0
            for m in modes:
                if is_intraday_day:
                    p, mm = fl.picks_for_date(
                        ical, ipanels, pd.Timestamp(d), m, require_major,
                        cfg=s.cfg)
                else:
                    p, mm = fl.picks_for_date(
                        cal, panels, pd.Timestamp(d), m, require_major,
                        cfg=s.cfg)
                n += mm['n']
                pieces.append(p.assign(pick_mode=m, pick_label=mode_names[m]))
            if pieces:
                picked = pd.concat(pieces, ignore_index=True)
            else:
                picked = pd.DataFrame(columns=['rank_no', 'opportunity'])
            s.picks_data = (picked, {'n': n}, d, modes, require_major,
                            is_intraday_day and iinfo is not None, iinfo)
            s.picks_sig = json.dumps(
                [str(tag), str(d), modes, require_major, is_intraday_day],
                ensure_ascii=False, default=str)
    if not s.get('picks_data'):
        rx_info('选择日期与候选口径后点击查询。')
        return
    picked, meta, d, modes, require_major, was_intraday, iinfo = s.picks_data
    cur_sig = json.dumps(
        [str(tag), str(d), modes, require_major, is_intraday_day],
        ensure_ascii=False, default=str)
    if s.picks_sig != cur_sig:
        rx_info('上方口径或左侧参数已修改，当前为上次查询结果；点击查询以更新。')
    if was_intraday and iinfo is not None:
        st.warning('盘中实时 · 数据时间 %s（非收盘口径，收盘后请重新查询）'
                   % iinfo.get('tick_time'))
    mode_txt = '、'.join(mode_names.get(m, m) for m in modes)
    st.write('当日 %s 候选 %d 个' % (mode_txt, meta['n']))
    disp_cols = ['rank_no', 'pick_label', 'opportunity', 'code', 'name', 'close',
                 'activity_score', 'clean_level', 'level_conf', 'range_pct',
                 'range_z', 'emotion_score', 'resonance', 'position_mult',
                 'tradable']
    disp_cols = [c for c in disp_cols if c in picked.columns]
    if len(picked):
        view = show_df(picked[disp_cols], code_cols=['code']).rename(
            columns={'rank_no': '序号',
                     'pick_label': '口径',
                     'opportunity': '机会', 'tradable': '可交易'})
        st.dataframe(view, use_container_width=True, hide_index=True)
        _fn = '_'.join(modes) if modes else 'multi'
        _dl1, _dl2 = st.columns([1, 1], gap='small')
        with _dl1:
            st.download_button('下载名单（UTF-8）', data=to_csv(picked),
                               file_name='factor_lab_picks_%s_%s.csv'
                               % (pd.Timestamp(d).strftime('%Y%m'), _fn),
                               use_container_width=True)
        with _dl2:
            st.download_button('下载名单（GBK）', data=to_csv_gbk(picked),
                               file_name='factor_lab_picks_%s_%s_gbk.csv'
                               % (pd.Timestamp(d).strftime('%Y%m'), _fn),
                               use_container_width=True,
                               help='若 UTF-8 版本打开股票名称乱码，请下载此版本。')
    else:
        rx_info('当日无符合条件的标的。')


# -------------------- Tab4 周期归因 --------------------

def tab_attribution(state):
    cal, panels, provider, tag = state
    s = st.session_state
    if st.button('▶ 运行归因', type='primary'):
        with st.spinner('回测计算中...'):
            res = fl.backtest_once(
                s.cfg, s.start, s.end, user_id=s.user_id,
                fee_bps=s.fee_bps, provider=provider,
                panels_state=(cal, panels, tag))
        s.last_attr = res
        s.last_attr_tag = tag
        s.last_attr_sig = run_signature()
        s.last_attr_snap = snapshot(res['cfg'], tag)
    res = s.get('last_attr')
    if res is None:
        rx_info('按当前侧边栏参数运行后展示逐年/月度归因。')
        return
    tag = s.last_attr_tag
    if s.get('last_attr_sig') != run_signature():
        rx_info('左侧参数已修改，当前为上次运行结果；点击运行以更新。')
    st.markdown('**逐年收益**')
    st.dataframe(res['yearly'], use_container_width=True)

    mret = res['monthly']
    if mret is not None and len(mret):
        import plotly.express as px
        fig = px.imshow(
            (mret * 100).values,
            x=['%d月' % i for i in mret.columns],
            y=[str(i) for i in mret.index],
            text_auto='.1f', color_continuous_scale='RdYlGn',
            aspect='auto')
        fig.update_layout(height=260, coloraxis_colorbar_title='%')
        st.plotly_chart(fig, use_container_width=True)
    show_snapshot(s.last_attr_snap)


# -------------------- Tab5 自选池管理 --------------------

def _read_import_table(up):
    """读取导入文件，兼容真正的 .xls 以及同花顺常见的“伪 xls”。

    同花顺部分导出文件后缀为 .xls，实际是 GBK 编码的制表符分隔文本
    （文件开头常见“代码\\t”），Excel 能打开但 xlrd 无法识别。
    故：先按真 xls 解析，失败再按纯文本读，并自动嗅探分隔符、逐编码回退；
    CSV 同样可能是 GBK 编码，一并兼容。
    """
    raw = up.read()
    import io
    if up.name.lower().endswith('.xls'):
        try:
            return pd.read_excel(io.BytesIO(raw), dtype=str,
                                 engine='xlrd').fillna('')
        except Exception:
            pass
    last_err = None
    for enc in ('utf-8-sig', 'gbk', 'gb18030'):
        try:
            return pd.read_csv(io.BytesIO(raw), dtype=str, sep=None,
                               engine='python', encoding=enc).fillna('')
        except Exception as e:
            last_err = e
    raise last_err


def tab_watchlists(state):
    st.markdown('<span class="rxlib-watch-marker" style="display:none"></span>',
                unsafe_allow_html=True)
    st.subheader('自选池管理')
    st.caption('在此新建、导入、修改你自己的股票池；保存在 config/watchlists 目录，不写入数据库。')
    names = ls.list_watchlists()
    col1, col2 = st.columns([1, 2])
    with col1:
        name = st.text_input('新建 / 选择股票池名称', value='', placeholder='例如：我的观察池')
        if st.button('载入该池') and name:
            st.session_state.wl_name = name
        if names:
            st.caption('已有股票池：')
            for n in names:
                if st.button(n, key='wlbtn_%s' % n):
                    st.session_state.wl_name = n
    cur_name = st.session_state.get('wl_name') or name
    if not cur_name:
        rx_info('输入名称并点击“载入该池”，或在左侧点击已有股票池。')
        return
    st.markdown('**当前编辑：%s**' % cur_name)
    df = ls.load_watchlist(cur_name)

    up = st.file_uploader('导入文件（CSV / Excel .xls，含代码列，可带 SH/SZ 前缀）',
                          type=['csv', 'xls'])
    if up is not None:
        try:
            imp = _read_import_table(up)
            # 代码列：优先 rxcode / code / 代码，否则取第一列（同花顺导出表头即“代码”）
            code_col = next((c for c in imp.columns
                             if str(c).strip().lower() in ('rxcode', 'code', '代码')),
                            imp.columns[0])
            rxcodes = [fl.normalize_rxcode(x) for x in imp[code_col]]
            name_col = next((c for c in imp.columns
                             if str(c).strip() in ('name', '名称', '名字')), None)
            names = list(imp[name_col]) if name_col is not None else [''] * len(imp)
            rows = [dict(rxcode=rx, name=nm)
                    for rx, nm in zip(rxcodes, names) if rx]
            imp_df = pd.DataFrame(rows, columns=['rxcode', 'name'])
            df = pd.concat([df, imp_df],
                           ignore_index=True).drop_duplicates('rxcode')
            st.success('已导入 %d 条，记得点下方保存。' % len(imp_df))
        except Exception as e:
            st.error('导入失败：%s；请确认是 CSV 或 Excel 表格（.xlsx 请另存为 .xls 或 CSV）。' % e)

    pasted = st.text_area('批量粘贴代码（逗号/空格/换行分隔，可带 SH/SZ 前缀）', height=80)
    if st.button('追加粘贴的代码') and pasted:
        import re
        toks = re.split(r'[,\s;，；]+', pasted)
        rxcodes = [c for c in (fl.normalize_rxcode(x) for x in toks) if c]
        add = pd.DataFrame([dict(rxcode=rx, name='') for rx in rxcodes],
                           columns=['rxcode', 'name'])
        df = pd.concat([df, add], ignore_index=True).drop_duplicates('rxcode')
        st.success('追加 %d 条，记得点保存。' % len(add))

    st.markdown('**编辑名单（可直接增删改）**')
    edit_df = df[['rxcode', 'name']].copy()
    edit_df.insert(0, '序号', range(1, len(edit_df) + 1))
    edited = st.data_editor(edit_df, num_rows='dynamic',
                            use_container_width=True,
                            hide_index=True,
                            column_config={
                                '序号': st.column_config.NumberColumn(
                                    '序号', width='small', disabled=True),
                                'rxcode': st.column_config.TextColumn(
                                    'rxcode', width='medium'),
                                'name': st.column_config.TextColumn('名称', width='medium')})
    c = st.columns(4)
    if c[0].button('💾 保存', type='primary'):
        n = ls.save_watchlist(cur_name, edited)
        st.success('已保存 %d 只到股票池「%s」。' % (n, cur_name))
    if c[1].button('🗑 删除该股票池'):
        ls.delete_watchlist(cur_name)
        st.session_state.pop('wl_name', None)
        st.success('已删除，刷新页面生效。')
    c[2].download_button('下载该池 CSV（UTF-8）',
                         data=to_csv(edited[['rxcode', 'name']]),
                         file_name='%s.csv' % cur_name)
    c[2].download_button('下载该池（Excel 中文兼容 GBK）',
                         data=to_csv_gbk(edited[['rxcode', 'name']]),
                         file_name='%s_gbk.csv' % cur_name,
                         help='若用 UTF-8 版本打开中文乱码，请下载此版本。')


# -------------------- Tab6 系统设置 --------------------

def tab_settings(state):
    st.subheader('系统设置')
    settings = ls.load_settings()

    st.markdown('**直连数据库参数**')
    db = ls.get_db_config()
    c1, c2 = st.columns(2)
    host = c1.text_input('主机地址 host', value=db['host'])
    port = c2.number_input('端口 port', min_value=1, max_value=65535,
                           value=int(db.get('port', 3306)), step=1)
    database = c1.text_input('数据库 database', value=db['database'])
    user = c2.text_input('用户名 user', value=db['user'])
    password = c1.text_input('密码 password', value=db['password'], type='password')
    cc = st.columns(3)
    if cc[0].button('🔌 测试连接'):
        trial = dict(host=host, port=int(port), database=database,
                     user=user, password=password)
        try:
            n = ls.test_connection(trial)
            st.success('连接成功！stock_base 共 %d 条记录。' % n)
        except Exception as e:
            st.error('连接失败：%s' % e)
    if cc[1].button('💾 保存连接参数'):
        platform_env.save_db_config(dict(host=host, port=int(port),
                                         database=database, user=user,
                                         password=password))
        st.success('连接参数已保存到 config/config.ini，刷新页面后生效。')

    st.divider()
    st.markdown('**API 数据服务参数**')
    st.caption('当左侧"数据提供层"选择 API 时使用；base_url 为服务版本根地址，令牌以 Bearer 方式携带。')
    api = platform_env.get_api_config()
    api_base = st.text_input('API 地址 base_url', value=api['base_url'],
                             placeholder='https://host/api/v1')
    api_token = st.text_input('访问令牌 token', value=api['token'],
                              type='password')
    ac1, ac2 = st.columns(2)
    api_timeout = ac1.number_input('超时（秒）', min_value=1, max_value=300,
                                   value=int(api.get('timeout', 10)), step=1)
    api_verify = st.checkbox('校验 HTTPS 证书', value=bool(api.get('verify_ssl', True)))
    apc = st.columns(3)
    if apc[0].button('🔌 测试 API'):
        trial = dict(base_url=api_base, token=api_token, timeout=int(api_timeout),
                     verify_ssl=api_verify)
        try:
            import data_provider
            prov = data_provider.ApiProvider(**trial)
            try:
                meta = prov.meta()
                latest = meta.get('latest_trade_date') or prov.latest_trade_date()
                exp = str(meta.get('key_expires_at') or '').strip()
                if exp:
                    exp_dt = pd.to_datetime(exp)
                    days = int((exp_dt.normalize() -
                                pd.Timestamp.now().normalize()).days)
                    tip = 'Token 有效期至 %s（剩余 %d 天）' % (
                        exp_dt.strftime('%Y-%m-%d'), days)
                else:
                    tip = 'Token 长期有效（未设到期时间）'
                st.success('连接成功！最新交易日：%s；%s' % (latest, tip))
            finally:
                prov.close()
        except Exception as e:
            st.error('连接失败：%s' % e)
    if apc[1].button('💾 保存 API 参数'):
        platform_env.save_api_config(dict(base_url=api_base, token=api_token,
                                          timeout=int(api_timeout),
                                          verify_ssl=api_verify))
        get_panels.clear()
        st.success('API 参数已保存到 config/config.ini，面板将使用新参数重新加载。')

    st.divider()
    st.markdown('**本地 CSV 导入数据源**')
    st.caption('当左侧"数据提供层"选择"本地 CSV 导入"时使用。日线 K 线按月组织为 '
               'daily_YYYYMM.csv（如 daily_202610.csv）。下面两种方式二选一即可：'
               '① 用"上传文件"直接导入（推荐）；② 或自行把 CSV 放进下方目录。')
    import_cfg = platform_env.get_import_config()
    import_dir = st.text_input('导入数据目录 data_dir', value=import_cfg['data_dir'],
                               placeholder=r'D:\rxdata\kline')
    upload_files = st.file_uploader(
        '上传月度 CSV（可多选，文件名须为 daily_YYYYMM.csv）',
        type=['csv'], accept_multiple_files=True)
    uc = st.columns(2)
    if uc[0].button('📤 保存上传文件到导入目录'):
        if not upload_files:
            st.warning('请先在上方选择要上传的 CSV 文件。')
        else:
            import os
            import re
            target = (import_dir or '').strip()
            if not target:
                target = platform_env.get_import_config()['data_dir']
            try:
                os.makedirs(target, exist_ok=True)
            except Exception as e:
                st.error('无法创建导入目录 %s：%s' % (target, e))
            else:
                saved, skipped = [], []
                for uf in upload_files:
                    fn = uf.name
                    if re.fullmatch(r'daily_\d{6}\.csv', fn, re.IGNORECASE):
                        with open(os.path.join(target, fn), 'wb') as f:
                            f.write(uf.getvalue())
                        saved.append(fn)
                    else:
                        skipped.append(fn)
                get_panels.clear()
                msg = '已保存 %d 个文件到 %s：%s' % (len(saved), target, '、'.join(saved))
                if skipped:
                    msg += '；跳过（文件名不符 daily_YYYYMM.csv）：%s' % '、'.join(skipped)
                st.success(msg)
    try:
        avail = sorted(dp.get_provider(
            'import', data_dir=import_dir)._available_months())
        uc[1].caption('已识别月度数据：%s'
                      % ('、'.join(avail) if avail else '未识别到 daily_*.csv'))
    except Exception as e:
        uc[1].caption('目录扫描失败：%s' % e)
    if st.button('💾 保存导入目录'):
        platform_env.save_import_config({'data_dir': import_dir})
        get_panels.clear()
        st.success('导入目录已保存到 config/config.ini，面板将按新目录重新加载。')

    st.divider()
    st.markdown('**登录密码**')
    st.caption('需验证当前密码；保存后只落 PBKDF2 哈希，本地不保存明文。改密成功后旧登录令牌立即失效。')
    op = st.text_input('当前密码', type='password', value='', key='set_pwd_old')
    np1 = st.text_input('新密码', type='password', value='', key='set_pwd1')
    np2 = st.text_input('确认新密码', type='password', value='', key='set_pwd2')
    st.caption('新密码长度 %d~%d 位，首尾不能含空格。'
               % (ls.MIN_PASSWORD_LEN, ls.MAX_PASSWORD_LEN))
    if st.button('修改密码'):
        strength_err = ls.validate_password(np1)
        if not op:
            st.warning('请输入当前密码。')
        elif not np1:
            st.warning('新密码不能为空。')
        elif strength_err:
            st.error(strength_err)
        elif np1 != np2:
            st.error('两次输入的新密码不一致。')
        elif np1 == op:
            st.warning('新密码不能与当前密码相同。')
        else:
            try:
                ls.change_password(op, np1)
            except ValueError as e:
                st.error(str(e))
            else:
                ls.rotate_cookie_secret()
                get_panels.clear()
                for k in ('set_pwd_old', 'set_pwd1', 'set_pwd2'):
                    st.session_state.pop(k, None)
                components.html(
                    _set_cookie_js(ls.COOKIE_NAME, ls.make_auth_token(),
                                   ls.COOKIE_DAYS), height=0)
                st.success('密码已更新。')

    st.divider()
    st.markdown('**面板缓存版本**')
    st.caption('改版本号会让旧缓存判定失效并自动重建。代码结构版本前进时本值会'
               '自动前向同步，通常无需手工调整；手动调大可强制重建。')
    cur_ver = ls.get_panel_version()
    new_ver = st.number_input('版本号', min_value=1, value=cur_ver, step=1)
    if st.button('保存版本号'):
        settings = ls.load_settings()
        settings['panel_version'] = int(new_ver)
        ls.save_settings(settings)
        get_panels.clear()
        st.success('版本号已设为 %d，将重新加载/构建面板。' % new_ver)
    st.divider()
    st.markdown('**面板磁盘持久化**')
    st.caption('开启后，组装好的面板会保存到本地，重启平台无需重新计算（占用一定磁盘空间）；关闭则只保留内存缓存，重启后重建。')
    cache_cfg = platform_env.get_cache_config()
    disk_on = st.checkbox('面板保存到本地磁盘',
                          value=bool(cache_cfg.get('panel_disk_cache', True)))
    if st.button('保存磁盘缓存设置'):
        platform_env.save_cache_config({'panel_disk_cache': disk_on})
        get_panels.clear()
        st.success('已%s面板磁盘持久化，面板将按新设置重新加载。'
                   % ('开启' if disk_on else '关闭'))


# -------------------- Tab7 操作说明 --------------------

_DIAGRAM_TOKEN = '@@CLEAN_LEVEL_DIAGRAM@@'


def _render_clean_level_diagram():
    """在 7.2 干净价位节渲染一张真实示意图（根目录手动维护的 clear_level.jpg）。
    streamlit 会剥掉 markdown 内联 SVG，故用 st.image 单独加载本地图片。"""
    img = os.path.join(os.path.dirname(os.path.dirname(_HERE)), 'clear_level.jpg')
    st.image(img, caption='价格反复回踩同一根支撑线、每次都守住（图中 3 次触及），'
                          '这根线就被「验证」得越可靠，这才是干净价位。',
             width='stretch')


def tab_help(state):
    st.markdown('<span class="rxlib-help-marker" style="display:none"></span>',
                unsafe_allow_html=True)
    st.subheader('操作说明')
    st.caption('本页是平台完整使用手册；各控件旁的 ❔ 图标也有即时提示。')
    if _DIAGRAM_TOKEN in HELP_TEXT:
        head, _, tail = HELP_TEXT.partition(_DIAGRAM_TOKEN)
        st.markdown(head, unsafe_allow_html=False)
        _render_clean_level_diagram()
        st.markdown(tail, unsafe_allow_html=False)
    else:
        st.markdown(HELP_TEXT, unsafe_allow_html=False)


HELP_TEXT = """
## 一、平台是做什么的

rxlib 量化研究平台基于 **Lance Breitstein「支撑与阻力五要点」** 交易方法论，全部信号都由纯 **OHLCV
K 线**计算，在历史数据上验证「活跃标的 → 干净价位 → 盘整 / 情绪 → 五要点信号 → 多周期共振」
五层流水线，并可查看**某一交易日的机会名单**；对开通盘中权限的 API Key，也支持在交易日盘中
用实时行情重算各标的指标（以上均为**收盘口径**，默认以此为准）。

**五要点（五层计算流水线，均围绕支撑与阻力展开）**：

1. **活跃度（Activity）**：只用活跃标的 —— ATR 倍数、量能倍数、跳空 + 横截面活跃度评分；
2. **干净价位（Clean Level）**：只在干净价位进场 —— 枢轴位识别 / 价位置信度 / 突破速度 / 锚定 VWAP；
3. **适当盘整（Consolidation）**：区间压缩 / 净漂移 / 回归斜率 / 区间均匀度 / 量价结构；
4. **情绪反应（Emotion）**：布林带偏离 / 量能情绪 / 加速度 / 连续同向 K 线 + 入场确认（V 形右侧）；
5. **多周期一致（Resonance）**：日→周→月趋势合成 / 共振评分 / 仓位缩放 / 关键位重合。

全部信号都由唯一内核 `factor_lab` + `breitstein` 计算（主线式、模型写死在文件里），
界面只负责收集参数、调用内核、展示结果，**全程对数据库只读，不写任何业务数据表**。

**基本使用流程**：输入访问密码登录 → 左侧侧边栏设置数据源与参数 → 进入对应标签页 → 点击「运行」。

## 二、登录、退出与修改密码

- **登录**：打开平台需输入访问密码。登录成功后会在浏览器保存一个 30 天有效的登录令牌，
  期间再次打开无需重复输入。连续输错 5 次会临时锁定 5 分钟。
- **退出登录**：左侧侧边栏底部有「退出登录」按钮，点击后清理本机登录态并回到登录页，
  同时清空本次会话的参数、运行历史等临时数据。
- **修改密码**：在「系统设置 → 登录密码」中输入当前密码和新密码，需两次输入一致。
- **密码保存在哪里**：密码**只以 PBKDF2 哈希**（加随机盐、迭代 20 万次）保存在本机
  `config/_factorlab_settings.json`，系统不保存明文，也无法从哈希反推密码。

## 三、标签页说明

| 标签页 | 作用 |
|---|---|
| **历史回测** | 用当前参数在所选区间完整跑一遍，给出收益/风险指标、净值/回撤曲线、单计划交易明细 |
| **批量网格** | 一次跑多组参数（presets 4 个具名预设 / grid 24 组）并排名，用于横向对比 |
| ~~**单日回测**~~（已下线） | 原为查看某交易日的机会名单（活跃 TopN / 盘整突破 / 投降+收复AVWAP / 共振）及**盘中实时选股**；实现保留、入口暂时隐藏 |
| **周期归因** | 展示逐年收益与月度收益热力图 |
| **自选池管理** | 新建/导入/编辑你自己的股票池（本地保存） |
| **系统设置** | 配置数据源/API 参数、修改密码、面板缓存版本、磁盘持久化 |
| **操作说明** | 本手册 |

## 四、数据源（API 模式 / MySQL 直连）

平台支持两种取数方式，在「系统设置」中配置：

- **API 模式（默认）**：通过一个 API Key 从 rxlib_api 服务取数；Key 的权限决定可用功能，
  **盘中数据权限由 Key 自动识别**。Breitstein 五要点指标全部在本地计算，API 只负责取数。
- **MySQL 直连模式**：直接连接本机/局域网行情库，支持收盘日线与盘中快照。

配置信息保存在本机 `config/config.ini`，升级版本不会被覆盖；数据库连接用完即关、只读。

## 五、股票池 / 区间参数

- **股票池**：可选「全 A」「按板块（沪市主板/深市主板/创业板/科创板/北交所）」、
  你在「自选池管理」里建立的自定义池，以及内置的 **「科创50（测试）」**。
- **科创50（测试）**：是**固定快照**——取的是**某个时点**的科创50（上证科创板50成份指数，000688.SH）
  全量 **50 只**成份股（依据上交所 588090 科创50ETF 申购赎回清单固化）。名单一经固化便不再变动，
  **不会**因为真实指数后续的成份股调入/调出而自动更新。若要使用最新成份，需要人工更新
  `factor_lab.py` 中的 `TEST_POOL_CODES`，并按新名单重新生成对应的 `data/import/daily_2026MM.csv`
  导入数据。
- **A 股可交易约束**：默认排除 ST（`exclude_st=True`）、要求最小上市
  `min_list_days=60` 交易日（可配）方可进入名单并参与撮合。
- **短历史降级**：压缩历史窗口 `hist_window` 默认 252，数据不足时自动回退 120/60；
  多周期快慢线 MA10/MA30 数据不足时回退 MA3/MA5，再不足则记 0（趋势不参与共振）。
- **开始/结束日期**：回测区间。系统会自动向开始日期之前多取约 2 个月数据用于预热。
- **并行进程数**：首次构建面板时使用的 CPU 进程数；Windows 下若卡住可设为 1。

## 六、Lance B 支撑与阻力五要点：指标与参数详解

下面按五要点逐层说明字段口径。默认值均与内核 `breitstein.default_params()` /
`factor_lab.base_cfg()` 严格一致，界面可修改并在本次运行时生效。

### 6.1 活跃度（Activity）

> 作用：作为最外层漏斗，只在足够活跃的标的中找机会，过滤掉流动性差、无人问津、波动过低的个股。
> 它衡量"当天这根 K 线有多热闹"（放量、大涨大跌、跳空），**热度越高分越高**——想清楚这点很关键，
> 因为热度顶点往往也最接近短线顶部（见下方"常见误区"）。

四个子信号（任一维度越过阈值即视为该维活跃，再合成总热度）：

> **运行时 OR 判定（已接线）：** `atr_ratio_th` / `vol_ratio_th` / `gap_th` 不只是某个布尔列的"有或无"，
> 而是与总入口门槛 `activity_th` 一起在**每次点「运行历史回测」时即时判定**：
> `activity_raw ≥ activity_th` **且**（ATR比/量比/跳空三腿**至少任一**超过各自阈值）即算活跃。
> 改左侧任一滑块 + 点运行即会改变选中与回测结果，无需重建面板。

| 参数 | 公式 | 直觉 | 默认门槛 | 门槛设高＝更严格 |
|---|---|---|---|---|
| **ATR_RATIO（active_atr）** | `ATR(14) ÷ median(ATR20)` | 当根波动相对自身历史是否放大（异动有没有量） | `atr_ratio_th=2.0` | 只留波动显著放大的 |
| **VOLUME_RATIO（active_vol）** | 当根量 ÷ `SMA20(vol)` | 当根是否放量 | `vol_ratio_th=1.8` | 只留明显放量的 |
| **GAP_PCT（active_gap）** | `(Open−PrevClose)/PrevClose` | 开盘是否跳空 | `gap_th=±2%` | 只留大跳空的 |
| **ACTIVITY_RAW（activity_raw）** | `0.4·ATR + 0.4·Vol + 0.2·\\|Gap\\|` | 三因子归一后的总热度 | 主门槛 `activity_th=1.5` | 主门槛越高，只剩最热的一批 |

- **ACTIVITY_RAW**：三因子规范到与门槛同量级后加权合成；`activity_th` 是**总入口门槛**，管道A 内部与横截面排序都用它；三腿（atr/vol/gap）在总门槛之上做 OR 细筛。
- **ACTIVITY_SCORE**：对当日全市场 `activity_raw` 做 z 归一后的横截面分数，用于"活跃 TopN"榜（`topn=10`、上限 25 只）。
- 活跃度层窗口默认 `act_window=14`。

> ⚠️ 常见误区：**active（活跃 TopN）只是"当天最热的股票名单"，是入场漏斗的最外层，不是买点。**
> 热度榜第一名往往是当天放量大涨、正逼近短线顶的票（典型如恒玄科技 9/22 活跃度峰值≈见顶、汉邦科技 9/21-9/22 峰值≈箱体顶，
> 之后次日即回落）。要用它做真正入场信号，必须再叠加管道A（盘整突破）或管道B（情绪反转）确认，
> 或叠加多头共振（`resonance>=0`）做方向过滤，而不是单独看活跃榜。

### 6.2 干净价位（Clean Level）

| 字段 | 含义 / 默认口径 |
|---|---|
| **pivot_high/pivot_low** | 枢轴高/低位，`_find_extrema`、`pivot_order=5`（左右各 5 根确认） |
| **LEVEL_SUPPORT/RESISTANCE** | 识别出的支撑位 / 阻力位 |
| **LEVEL_CONF_S/R、CLEAN_LEVEL** | 价位置信度 = 触及概率 × 保持概率；`conf_window=100`、`touch_tol=0.002`、`conf_th=0.10`；touches=0 时置信记 0（不进干净价位） |
| **BREAKOUT_ROC** | 突破速度 `ROC=(C[t+N]−C[t])/C[t]/N`；`roc_n=3`、`roc_min=0.01` |
| **AVWAP / AVWAP_SIDE** | 锚定平均价 `∑(TP·V)/∑V`；锚点默认取投降 K 线当日开盘（`avwap_anchor='open'`） |

> **一句话理解「干净价位」**：只在一个"价格干净、结构清晰"的区域进场——就是价格在里面
> 震荡过的区间（被反复触及的枢轴支撑/阻力之间）。理由是：**在这里进出点离得近、止损小、
> 方向被历史验证过**，而不是追在半空中、听见噪声就乱动。
>
> - **为什么只在这些价位进**：枢轴位是被市场用真金白银反复确认过的支撑/阻力，价格在附近
>   成交密集、波动收敛。在箱体内的「干净价位」买入，止损就放在箱体边沿，空间小、胜率结构好。
> - **凭什么它会是好买点**：历史里这些关键位多次有效（`LEVEL_CONF`＝触及概率×保持概率），
>   说明市场对这个价位有共识；突破箱顶（管道A）或触碰支撑后放量反弹、收复锚定 AVWAP（管道B）
>   都是在"干净价位确认启动"的信号。
> - **反之**：远离这些枢轴位的「浮空价位」成交稀疏、噪声大，越界追高极易被一根大阴线扫损，
>   这正是前面 7.1 活跃榜"最热≠买点"的原因。
>
> 下图只讲**"干净价位"本身**——价格反复回踩同一根支撑、每次都守住，这根线就被「验证」得越
> 可靠，这才是"价格干净、结构清晰"的区域。至于**两种入场**不在这张图上：管道A 是突破箱顶、
> 管道B 是触碰支撑后放量反弹并收复锚定 AVWAP（见上方"凭什么它会是好买点"，以及 7.3–7.5 各要点）。

@@CLEAN_LEVEL_DIAGRAM@@

### 6.3 适当盘整（Consolidation）

| 字段 | 含义 / 默认口径 |
|---|---|
| **RANGE_PCT / COMPRESSED** | 区间 N 根价格分位差；`comp_window=10` |
| **RANGE_Z** | 区间压缩 z 分位；压缩判定 `RANGE_PCT<pct_th(0.10)` 或 `RANGE_Z<z_th(−1.0)` |
| **NET_DRIFT** | `(C[-1]−C[0])/range_N` 绝对漂移；`drift_th=0.3`；range_N==0 时记 0（平台适配） |
| **REG_SLOPE** | 线性回归斜率 ÷ 均价；`slope_th=0.001`；均值为 0 时记 NaN（平台适配） |
| **CV / UNIFORM** | 箱体内 `CV<0.5` 且单日 ≤ 箱体 50%；`cv_th=0.5`、`cv_day_ratio=0.5` |
| **VOLUME_TREND_OK** | 区间内量能递减（斜率 < 0），量价结构健康 |
| **历史窗口** | `hist_window=252`；短历史自动回退 120/60 |

### 6.4 情绪反应（Emotion）

| 字段 | 含义 / 默认口径 |
|---|---|
| **BB_Z / EMOTION_EXTREME** | `(C−SMA20)/(2·STD20)` 布林带 z；`bb_window=20`、默认 `abs(bb_z)>2.0`（等价 4σ，保留原文阈值），界面可配 1.5/1.0 |
| **SENTIMENT_SCORE** | 量能情绪 `vol_z×direction`；`sentiment_th=2.5` |
| **ACCELERATION / ACCEL_Z** | 价格速度 / 加速度；accel_z 用 20 根滚动归一（`accel_window=20`） |
| **CONS_DOWN** | 连续同向 K 线计数；`cons_down_min=3`、`cons_down_max=5` |
| **CAPITULATION_BAR** | 投降 K 线：加速下跌且放量 ≥ `capitulate_vol_mult(2.0)×SMA20` |
| **RECLAIM_AVWAP** | 收复锚定 AVWAP（`close>avwap`），构成入场确认 |
| **EMOTION_SCORE** | `0.35` 布林 + `0.35` 量能情绪 + `0.30` 加速度；极端阈值 `emotion_extreme_th=2.5`；管道 B 筛选门槛 `pipeline_b_emotion_th=2.0` |

### 6.5 多周期一致（Resonance）

| 字段 | 含义 / 默认口径 |
|---|---|
| **DAILY/WEEKLY/MONTHLY_DIR** | 各周期趋势方向：快线 `ma_fast=10`、慢线 `ma_slow=30`（短历史回退 MA3/MA5）；周线只取最近已收盘 W-FRI、月线只取最近已结束自然月，**无未来函数** |
| **RESONANCE** | 日/周/月方向合成 ∈{−3..3} → 归一范围；分母为计入周期数 |
| **POSITION_MULT** | `1 + 0.25×(共振/3)`，用于仓位缩放 |
| **LEVEL_OVERLAP** | 关键位重合：日线 `daily_res_window=20`、周线 `weekly_res_window=4`、`res_overlap_tol=0.005`（<0.5%） |

### 6.6 双管道筛选与默认阈值小结

- **管道 A（盘整突破 breakout）**：活跃度 + 干净价位前置 → 区间压缩 → ROC 加速突破；
- **管道 B（情绪反转 reversal）**：活跃度 + 干净价位前置 → 情绪极端 → 投降 K 线 → 收复 AVWAP；
- 两管道共用前置、互不拦截，任一命中即产生机会；机会字段 `opportunity / activity_score /
  clean_level / level_conf / range_pct / range_z / emotion_score / resonance / position_mult`。

| 阈值 | 默认 | 阈值 | 默认 |
|---|---|---|---|
| ATR 倍数 | 2.0 | 价位置信度 | 0.6 |
| 量能倍数 | 1.8 | 突破 ROC | N=3 / ≥1% |
| 跳空 | ±2% | 布林带 z | >2.0（可配 1.5/1.0） |
| 活跃度门槛 | 1.5 | 情绪极端 | ≥2.5（管道 B ≥2.0） |
| TopN | 10（上限 25） | 投降量 | 2.0×SMA20 |
| 枢轴阶 | 5 | 连续同向 K | 3–5 |
| 置信窗口 / 容差 | 100 / 0.2% | 日/周/月趋势 | MA10/MA30 |
| 盘整窗口 | 10 | 单票上限 | 20% |
| 压缩判定 | pct<0.10 / z<−1.0 | | |

### 6.7 具名预设（一键载入）

左侧「预设一键载入」下拉框提供了 4 个 Breitstein 具名预设（预设键名沿用作者姓氏），各自代表一种五要点玩法，
只覆盖若干阈值参数，其余沿用当前自定义值：

| 预设 | 中文名 | 含义 / 玩法 |
|---|---|---|
| **active_momentum** | 活跃动量（默认） | 默认全面型。活跃度 + 管道A（盘整突破）主导，均衡覆盖全部要点，即"当前策略的出厂设置"。 |
| **capitulation_bounce** | 投降反弹 | 侧重管道B（情绪反转）。要求更极端情绪（`emotion≥2.0`）、放量更敏感（成交量放大 1.5 倍）、单只仓位更保守（上限 15%）。捕抓"恐慌投降后收复 AVWAP"的短线反弹。 |
| **narrow_consolidation** | 窄幅盘整 | 强调管道A的盘整压缩。压缩更严（区间更窄 `pct≤5%`、漂移更小 `drift≤0.2`），活跃度要求略降（`activity≥1.6`）。专做"箱体极窄、随时突破"的横盘起爆点。 |
| **strong_resonance** | 强共振 | 仅做多头共振（`allow_bear=False`），共振走弱即减仓，持有期拉长（最多 20 日）。追求五要点里"多周期共振"最扎实的中线行情。 |

一句话记忆：**活跃动量**＝均衡默认；**投降反弹**＝玩情绪反转（抄恐慌底）；
**窄幅盘整**＝玩盘整突破（赌横盘起爆）；**强共振**＝玩多周期共振（拿中线）。

### 6.8 要点条件开关（8 个）

「要点条件开关（可选）」面板**不是"五要点、一项一开关"**，而是把五要点拆成可单独开关的
细粒度拦截条件。默认全部勾选＝五要点全 AND。**每个开关都是一道"总闸"：勾选＝对应的数值
门槛参与选股（不达标就淘汰）；取消勾选＝该门槛被短路放行、不再看**（详见下面 7.8.1）。

8 个开关与五要点 / 管道的对照：

| 开关 | 要点 | 生效范围 | 作用 |
|---|---|---|---|
| `use_activity` | 要点一 活跃度 | 两管道前置 | `activity_raw≥activity_th`，先筛出足够活跃的标的 |
| `use_clean_level` | 要点二 干净价位 | 两管道前置 | 枢轴支撑/阻力位位置信度 `>conf_th` |
| `use_breakout` | 要点二 突破箱顶 | 管道A | 当日收盘**有效突破**箱体上沿 |
| `use_roc` | 要点二 突破速度ROC | 管道A | 突破斜率 `ROC>roc_min`，要求突破够快 |
| `use_compression` | 要点三 盘整压缩 | 管道A | 突破前处于盘整压缩态 |
| `use_emotion` | 要点四 极端情绪 | 管道B | `\\|emotion_score\\|≥` 情绪门槛 |
| `use_reversal_entry` | 要点四 反转入场确认 | 管道B | 投降 + 收复AVWAP 的入场信号 |
| `use_resonance` | 要点五 多周期共振 | 方向过滤 | 默认仅 `resonance>=0` 放行做多 |

### 6.8.1 开关 ＝ 数值门槛的"总闸"

每个开关都写成 `(not use_xxx or <该数值达标>)` 这种短路逻辑。所以：

- **勾选**（`use_xxx=True`）→ 后面的数值门槛才参与判定，不达标就跳过这条 K 线；
- **取消勾选**（`use_xxx=False`）→ `not use_xxx` 直接为真，**整条条件被放行**，对应数值门槛完全不看。

它不是"调高调低"的旋钮，而是"让不让这道数值门槛参与过滤"的总开关。**面板里即使存在该数值的输入框，开关取消后也不会用**。

### 6.8.2 两条管道如何判定（管道B 优先）

同一根 K 线按**管道B → 管道A** 顺序评估，**只命中其一**（B 成立就不再看 A）：

- **管道B（情绪反转 reversal）**需同时满足 4 条：
  `use_activity`（活跃度）＋ `use_clean_level`（支撑位信度 `conf_s>conf_th`）＋ `use_emotion`（极端情绪）＋ `use_reversal_entry`（投降并收复 AVWAP）。
- 若 B 不成立，再评估**管道A（盘整突破 breakout）**，需同时满足 5 条：
  `use_activity`（活跃度）＋ `use_breakout`（突破箱顶）＋ `use_compression`（盘整压缩）＋ `use_roc`（突破够快）＋ `use_clean_level`（阻力位信度 `conf_r>conf_th`）。
- **`use_resonance` 不属任何管道**，在最前面独立执行：勾选时要求 `resonance>=0`（做多方向）才放行，**两个管道都要先过这一关**。

### 6.8.3 逐个开关取消勾选 ＝ 放行什么

| 取消勾选 | 放行的数值约束 | 作用范围 |
|---|---|---|
| `use_activity` | 不再要求 `activity_raw≥activity_th` | 两个管道 |
| `use_breakout` | 不再要求收盘突破箱体上沿（即便未突破也可能进） | 管道A |
| `use_compression` | 不再要求突破前盘整压缩（可追连续放量拉升） | 管道A |
| `use_roc` | 不再要求突破斜率高（慢突破也可进） | 管道A |
| `use_clean_level` | 不再要求价位信度 `>conf_th` | 两个管道（`conf_s` 与 `conf_r`） |
| `use_emotion` | 不再要求情绪极端 | 管道B |
| `use_reversal_entry` | 不再是非要 V 形反转信号，只要情绪达标即可 | 管道B |
| `use_resonance` | 不再用多空共振方向过滤 | 所有入场 |

> 例：只想"看干净价位、不看突破够不够快" → 取消 `use_roc`，其余保持勾选：管道A 就只要求
> `use_breakout`＋`use_compression`＋`use_clean_level`。想更宽松"只要有支撑即可" → 连
> `use_breakout`（不要求突破）、`use_compression`（不要求盘整）一起取消，仅留 `use_clean_level` +
> `use_activity`，此时只要价位信度够且活跃就按管道B/A 入场。

**为什么要点二有 3 个开关？** 不是"干净价位包含了 3 个要点"，而是「要点二·干净价位」本身是一个
**前置条件组合**，实操被拆成三条独立拦截：`use_clean_level`（价位可信吗）→ `use_breakout`（有没有突破）
→ `use_roc`（突破够不够快）。拆开即可单独放宽其中一条（比如保留干净价位、但不再要求 ROC 那么快）。

一句话记忆：**2 条管道共用前置（要点一/二）＋ 管道A（突破箱顶/ROC/盘整压缩）＋ 管道B
（情绪/反转确认）＋ 方向过滤（多周期共振）＝ 8 个开关**。
明白这 8 个对应哪些要点、作用于哪条管道，你就能精确控制"哪些条件必须满足、哪些先放开看效果"。

## 七、入场、仓位与风控参数

| 控件 | 默认 | 含义 |
|---|---|---|
| **持仓槽位 topN** | 10 | 同时持有标的上限；目标权重 = 1/topN（如 1/10） |
| **position_multiplier** | — | 目标权重再乘 `1 + 0.25×(共振/3)`，放大高共振标的仓位 |
| **权重归一化** | 开 | 总额度 ≤ 100%，超限按比例缩放 |
| **单票上限 max_weight** | 20% | 任一只标的权重不超过 20% |
| **初始资金** | 100 万 | 按复利滚动（初始 100 万复利） |
| **涨停不买 / 跌停不卖** | 开 | A 股撮合约束：当日涨停不再新建买入、跌停不执行卖出 |
| **排除 ST** | 开 | 名单与撮合均剔除 ST（`exclude_st=True`） |
| **最小上市 min_list_days** | 60 | 上市满 60 交易日方可入选 |
| **ATR 止损 atr_mult** | 3.0 | 锚定价 − 倍数×ATR（可启用） |
| **结构/AVWAP 止损缓冲 avwap_stop_buf** | 0.02 | 锚定价下方 2% |
| **共振走弱退出 cons_weak_exit** | 开 / −2 | 共振分 ≤ −2 时考虑退出 |
| **活跃衰减退出 activity_decay_exit** | 关 / 3 天 | 活跃度连续衰减 3 天触发退出 |
| **整体出场 exit_enabled** | 开 | 主开关，可整体关闭出场逻辑 |

**出场优先级链**：固定止损(0) > 结构/AVWAP(1) > ATR(2) > 共振走弱(3) > 活跃衰减(4) > 到期(5)；
任一高优先级退出触发即按该理由离场，`_EXIT_RANK` 数值越小越优先。

> **本节为平台适配口径**：Breitstein 原文聚焦五要点指标与信号，**未定义止损/出场与仓位细则**；
> 上方止损、仓位归一化、A 股撮合约束与出场优先级均为平台为回测补齐的适配规则，勿视为原文观点。

**单边费率（bp）**：买入、卖出各收一次，1 bp = 0.01%，万分之 3 填 3；默认 0 不计费。

## 八、自选池怎么用

1. 进入「自选池管理」，输入名称点「载入该池」；
2. 可上传 CSV/Excel（含 `code` 列）、文本框批量粘贴、或在表格里直接增删改；
3. 保存后出现在左侧股票池下拉框（前缀「自定义 ·」）。
代码统一为 6 位数字，兼容 SH/SZ/BJ 前缀，不足 6 位自动补零。

## 九、本地缓存、面板版本与结果可溯源

- **两层缓存**：① 月片原始数据 `data/cache/_factorlab_kline_YYYYMM.pkl`；
  ② 面板（算好 Breitstein 五要点指标字段的结果），分内存与可选磁盘缓存。
- **面板版本号**：与代码 `PANEL_VERSION` 配套，结构升级时自动前向同步、旧缓存自动失效；
  日常改股票池/参数不需要手工调版本。发布裁剪可用 `prune-cache.ps1`。
- **可溯源**：每次运行提供参数快照（运行时间、股票池、区间、全部参数、数据源、缓存标签），
  可下载 JSON；交易明细 / 名单 / 排名均可下载 CSV（UTF-8-sig 与 GBK 两版）。

## 十、风险提示

- 本平台仅用于**交易学习与历史回测**，所有结果**不构成投资建议**；
- 历史回测不代表未来收益；Lance B 支撑与阻力五要点为量化辅助指标，阈值需结合行情回测校准，
  请自行承担交易风险。

---

### 术语表（中英对应）

| 中文 | 英文 |
|---|---|
| 支撑与阻力五要点 | Support & Resistance Five Points（Activity / Clean Level / Consolidation / Emotion / Resonance） |
| 活跃度 | Activity（ACTIVITY_RAW / ACTIVITY_SCORE） |
| 干净价位 | Clean Level（LEVEL_SUPPORT / RESISTANCE / CLEAN_LEVEL） |
| 适当盘整 | Consolidation（RANGE_PCT / RANGE_Z / CV） |
| 情绪反应 | Emotion（BB_Z / SENTIMENT_SCORE / EMOTION_SCORE） |
| 多周期一致 | Resonance（RESONANCE / POSITION_MULT） |
| 锚定成交均价 | AVWAP（Anchor VWAP） |
| 投降 K 线 | Capitulation Bar |
| 收复 AVWAP | Reclaim AVWAP |
| 突破速度 | Breakout ROC |
| 价位置信度 | Level Confidence |
| 连续同向 K 线 | Consecutive Same-direction Bars |
| 短历史降级 | Short-history Degradation |
| 管道 A / 管道 B | Pipeline A（breakout）/ Pipeline B（reversal） |
| 出场优先级链 | Exit Priority Chain（_EXIT_RANK） |
"""



# -------------------- 会话运行历史 --------------------

def show_history():
    hist = st.session_state.get('history', [])
    if not hist:
        return
    with st.sidebar.expander('会话运行历史（%d）' % len(hist)):
        for h in reversed(hist):
            st.caption('%s | %s | %s~%s' % (h['run_time'], h['source'],
                                            h['start'][5:], h['end'][5:]))


# -------------------- 主流程 --------------------

# 是否显示「单日回测」标签页（实现保留，改回 True 即可恢复入口）
SHOW_PICKS_TAB = False

def main():
    global_chrome_styles()
    mark_run_buttons()
    brand_header()
    login_gate()
    sidebar_controls()
    state = panels_or_message()
    show_history()
    _labels = ['历史回测', '批量网格', '周期归因',
               '自选池管理', '系统设置', '操作说明']
    _fn_aliases = [tab_single, tab_grid, tab_attribution,
                   tab_watchlists, tab_settings, tab_help]
    if SHOW_PICKS_TAB:
        _labels.insert(2, '单日回测')
        _fn_aliases.insert(2, tab_picks)
    tabs = st.tabs(_labels)
    funcs = _fn_aliases
    for tab, fn in zip(tabs, funcs):
        with tab:
            if state is None and fn not in (tab_settings, tab_help):
                rx_info('面板数据暂不可用，请在「系统设置」中检查数据源或更换 API Key。')
                continue
            fn(state)


if __name__ == '__main__':
    main()
