# -*- coding: utf-8 -*-
"""
兰斯·布雷特斯坦（Lance Breitstein）支撑与阻力五要点量化指标 —— 纯 Python 实现。

模块分层（与 spec FR 对应）：
  活跃度层    要点一：只交易活跃标的（ATR 倍数 / 量能倍数 / 跳空 + 综合评分）
  干净价位层  要点二：一个干净的价位（枢轴点识别 / 价位置信度 / 突破速度 ROC / 锚定 VWAP）
  盘整层      要点三：适当的盘整（区间压缩 / 净漂移 / 回归斜率 / 区间均匀度 / 量价结构）
  情绪层      要点四：情绪反应（布林带偏离 / 量能情绪 / 加速度 / 连续同向 K 线 + 入场确认）
  共振层      要点五：多周期一致（日/周/月趋势合成 / 共振评分 / 仓位缩放 / 关键位重合）

统一入口：calc_breitstein(df, params=None, benchmark=None)
筛选框架：breitstein_screen（双管道：A 盘整突破型 / B 情绪反转型）
横截面评分：cross_section_activity_score / topn_universe

本实现遵守：
  - 无 scipy：局部极值用手写滑动窗口比较 `_find_extrema`，斜率用 `np.polyfit(..., 1)`。
  - 无未来函数（NFR-1）：周线只取已收盘 W-FRI、月线只取已结束自然月；窗口/百分位/ROC 均为因果。
  - 短历史降级：压缩历史窗口 L 默认 252 不足回退 min(252,可用) 再回退 120/60；
    月/周趋势 MA10/MA30 不足回退 MA3/MA5，再不足该周期方向取 0 且不计入共振。
  - 数据不足统一输出 NaN / False / 0，不崩溃、不误发信号。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------

def default_params() -> dict:
    """返回 Breitstein 五要点全套参数默认值。"""
    return {
        # --- 活跃度层（要点一）---
        "atr_window": 14,            # ATR 主窗口
        "atr20_window": 20,          # ATR 中位数基准窗口
        "atr_ratio_th": 2.0,         # ATR_Ratio > 2.0 为活跃
        "vol_sma_window": 20,        # 量能基准 SMA 窗口
        "vol_ratio_th": 1.8,         # Volume_Ratio > 1.8 为活跃
        "gap_th": 0.02,              # |Gap_Pct| > 0.02 为跳空活跃
        "activity_th": 1.5,          # activity_raw 门槛
        "topn": 10,                  # 横截面 TopN 默认
        "topn_cap": 25,              # 横截面 TopN 上限
        # --- 干净价位层（要点二）---
        "pivot_order": 5,            # 枢轴点 order（前后各 N 根）
        "conf_window": 100,          # 价位置信度窗口
        "touch_tol": 0.002,          # 触及容差 0.2%
        "conf_th": 0.10,             # 干净价位置信度门槛
        "roc_n": 3,                  # 突破速度 ROC 窗口
        "roc_min": 0.01,             # 管道 A 突破速度门槛
        # --- 盘整层（要点三）---
        "comp_window": 10,           # 区间压缩盒 N
        "hist_window": 252,          # 压缩历史窗口 L
        "hist_fallback": (120, 60),  # 短历史回退窗口
        "pct_th": 0.10,              # 压缩百分位门槛
        "z_th": -1.0,                # 压缩 z 门槛
        "drift_th": 0.3,             # 净漂移门槛
        "slope_th": 0.001,           # 斜率/均价门槛
        "cv_th": 0.5,                # 变异系数门槛
        "cv_day_ratio": 0.5,         # 单日区间 ≤ 箱体 50%
        # --- 情绪层（要点四）---
        "bb_window": 20,             # 布林带窗口
        "bb_z_th": 2.0,              # |bb_z| > 2.0 极端（原文，可配 1.5/1.0）
        "sentiment_th": 2.5,         # |sentiment_score| 极端门槛
        "accel_window": 20,          # 加速度 zscore 归一窗口
        "emotion_extreme_th": 2.5,   # Emotion_Score 极端观察区间
        "pipeline_b_emotion_th": 2.0,# 管道 B 情绪门槛
        "capitulate_vol_mult": 2.0,  # 投降量 > 2×SMA20
        "cap_anchor_lookback": 30,   # 投降 AVWAP 锚点回看窗口
        "cons_down_min": 3,          # 识别连续阴线 3-5 根下限
        "cons_down_max": 5,          # 识别连续阴线 3-5 根上限
        # --- 多周期共振层（要点五）---
        "ma_fast": 10,               # 趋势快 MA
        "ma_slow": 30,               # 趋势慢 MA
        "ma_fast_degrade": 3,        # 短历史快 MA 回退
        "ma_slow_degrade": 5,        # 短历史慢 MA 回退
        "res_overlap_tol": 0.005,    # 关键位重合距离 <0.5%
        "daily_res_window": 20,      # 日线阻力 roll20
        "weekly_res_window": 4,      # 周线阻力 roll4
        # --- 入场 / 综合 ---
        "avwap_anchor": "open",      # AVWAP 锚点：open（投降 K 线当日开盘）/ prev_close
    }


def _merge_params(params: dict | None) -> dict:
    p = default_params()
    if params:
        p.update(params)
    return p


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _atr(df: pd.DataFrame, window: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def _find_extrema(values: np.ndarray, order: int):
    """手写 numpy 版 argrelextrema。

    返回 [(idx, price, kind, confirmed)]：
      kind = 'H'（局部高点/阻力）/ 'L'（局部低点/支撑）。
      confirmed = 两侧均有 order 根可用（true）；边界不足 order 根为未确认位（不用于触发）。
    相等值平台期：取平台中最后一个索引作为极值，避免重复。
    """
    n = len(values)
    res = []
    for t in range(n):
        lo = max(0, t - order)
        hi = min(n, t + order + 1)
        win = values[lo:hi]
        confirmed = (t - order >= 0) and (t + order < n)
        is_max = values[t] >= win.max()
        is_min = values[t] <= win.min()
        # 平台期非最后极值则跳过（相邻等值不重复计）
        if not np.isnan(values[t]):
            if is_max and t + 1 < n and np.isclose(values[t], values[t + 1]):
                continue
            if is_min and t + 1 < n and np.isclose(values[t], values[t + 1]):
                continue
            if is_max and t + 1 >= n:
                # 序列末端的最后一个极值保留
                pass
        if is_max:
            res.append((t, float(values[t]), "H", confirmed))
        if is_min:
            res.append((t, float(values[t]), "L", confirmed))
    return res


def _level_stats(price: float, kind: str, high, low, close, tol: float, window: int,
                 end: int | None = None):
    """对单个价位计算 level_confidence = touch_prob × hold_prob（因果口径）。

    kind='R' 阻力位（高价侧守）：触及 = high≥P(1−tol) 且 close<P；守住 = 触及后 close<P。
    kind='S' 支撑位（低价侧守）：触及 = low≤P(1+tol) 且 close>P；守住 = 触及后 close>P。
    窗口内 touches=0 → 置信度 0（NaN 安全归 0）。

    end：时刻索引（含该 bar），只使用 [end-window+1, end] 这段"已发生"区间，
    避免把整段序列末尾（未来）数据算进置信度导致前视。end=None 时默认取序列末尾。
    """
    n = len(high)
    last = end + 1 if end is not None else n
    start = max(0, last - window)
    W = last - start
    seg_hi = high.iloc[start:last].to_numpy()
    seg_lo = low.iloc[start:last].to_numpy()
    seg_c = close.iloc[start:last].to_numpy()
    if kind == "R":
        touch = (seg_hi >= price * (1 - tol)) & (seg_c < price)
        hold = touch & (seg_c < price)
    else:
        touch = (seg_lo <= price * (1 + tol)) & (seg_c > price)
        hold = touch & (seg_c > price)
    touches = int(touch.sum())
    if touches == 0:
        return 0.0, 0.0, 0.0
    touch_prob = touches / W
    hold_prob = int(hold.sum()) / touches
    return touch_prob, hold_prob, touch_prob * hold_prob


def _rolling_pct_rank(s: pd.Series, window: int) -> pd.Series:
    """因果滚动百分位：s[t] 在最近 window 个有效值中的秩百分位（NaN 不参与）。"""
    mp = min(20, window)
    return s.rolling(window, min_periods=mp).apply(
        lambda w: float(np.nanmean(w <= w[-1])) if len(w) else np.nan, raw=True
    )


def _hist_window(avail: int, p: dict) -> int:
    """短历史降级：返回压缩历史窗口 L（252 → 回退 120/60 → 可用长度）。"""
    if avail >= p["hist_window"]:
        return p["hist_window"]
    for w in p["hist_fallback"]:
        if avail >= w:
            return w
    return max(6, avail)


def _resampled_trend(dates, closes, freq: str, p: dict) -> pd.Series:
    """按 freq（'W-FRI'/'M'）合成已完整结束周期并计算趋势方向。

    返回以周期末（周五 / 月末）为索引的趋势方向序列；仅含已结束周期，
    通过 merge_asof(<= t) 落到日线可保证无未来函数。
    """
    tmp = pd.DataFrame({"date": pd.DatetimeIndex(dates), "close": closes.to_numpy()})
    if tmp.empty:
        return pd.Series(dtype=float)
    per = tmp["date"].dt.to_period(freq)
    g = tmp.groupby(per)["close"].agg("last")
    if len(g) < 3:
        return pd.Series(0.0, index=pd.DatetimeIndex([]), dtype=float)
    # 用周期末（周五 / 月末）作为已结束阈值
    ends = g.index.to_timestamp(how="end")
    idx = pd.DatetimeIndex(ends)
    trend = _trend_dir(pd.Series(g.to_numpy()), p)
    trend.index = idx
    return trend


def _asof_trend(daily_dates, trend: pd.Series) -> pd.Series:
    """把周期趋势按最近已结束周期(<=t)落到日线。"""
    if trend is None or len(trend) == 0:
        return pd.Series(0.0, index=pd.DatetimeIndex(daily_dates), dtype=float)
    # 统一日期单位为 ns，避免数据源(ns)与周期末(us/ms)在 merge_asof 上 dtype 不匹配
    left = pd.DataFrame({"date": pd.DatetimeIndex(daily_dates).as_unit('ns')}).reset_index()
    right = (pd.DataFrame({"date": pd.DatetimeIndex(trend.index).as_unit('ns'),
                           "val": trend.to_numpy()})
             .sort_values("date"))
    m = pd.merge_asof(left, right, on="date", direction="backward")
    out = m["val"].fillna(0.0).to_numpy()
    return pd.Series(out, index=pd.DatetimeIndex(daily_dates), dtype=float)


def _trend_dir(closes, p: dict) -> pd.Series:
    """趋势方向：1 if MA_fast>MA_slow else −1；短历史回退 MA3/MA5，仍不足取 0。"""
    closes = pd.Series(closes, dtype=float)
    n = len(closes)
    if n == 0:
        return pd.Series(dtype=float)
    fast, slow = p["ma_fast"], p["ma_slow"]
    d_fast, d_slow = p["ma_fast_degrade"], p["ma_slow_degrade"]
    if n >= slow:
        mf = closes.rolling(fast, min_periods=fast).mean()
        ms = closes.rolling(slow, min_periods=slow).mean()
    elif n >= d_slow:
        mf = closes.rolling(d_fast, min_periods=d_fast).mean()
        ms = closes.rolling(d_slow, min_periods=d_slow).mean()
    else:
        return pd.Series(0.0, index=closes.index, dtype=float)
    valid = mf.notna() & ms.notna()
    out = pd.Series(0.0, index=closes.index, dtype=float)
    if valid.any():
        out[valid] = np.where(mf[valid].to_numpy() > ms[valid].to_numpy(), 1.0, -1.0)
    return out


def _date_series(df: pd.DataFrame) -> pd.DatetimeIndex:
    if "trade_date" in df.columns:
        return pd.DatetimeIndex(pd.to_datetime(df["trade_date"]))
    return pd.DatetimeIndex(pd.to_datetime(df["date"] if "date" in df.columns else df.index))


# ---------------------------------------------------------------------------
# 要点一：活跃度层
# ---------------------------------------------------------------------------

def _activity_layer(out: pd.DataFrame, p: dict) -> pd.DataFrame:
    close = out["close"]
    open_ = out["open"]
    high = out["high"]
    low = out["low"]
    vol = out["volume"]

    # ATR_Ratio = ATR(14) / median(ATR(20))（中位数取截至 t 的可用历史，因果）
    atr14 = _atr(out, p["atr_window"])
    atr20 = _atr(out, p["atr20_window"])
    med20 = atr20.expanding(min_periods=1).median()
    ratio = atr14 / med20

    # Volume_Ratio = Volume / SMA(Volume,20)
    vol_sma = vol.rolling(p["vol_sma_window"], min_periods=p["vol_sma_window"]).mean()
    vol_ratio = vol / vol_sma

    # Gap_Pct = (Open − PrevClose) / PrevClose
    prev_close = close.shift(1)
    gap = (open_ - prev_close) / prev_close

    out["ATR_RATIO"] = ratio
    out["VOLUME_RATIO"] = vol_ratio
    out["GAP_PCT"] = gap
    out["ACTIVE_ATR"] = (ratio > p["atr_ratio_th"]).fillna(False)
    out["ACTIVE_VOL"] = (vol_ratio > p["vol_ratio_th"]).fillna(False)
    out["ACTIVE_GAP"] = (gap.abs() > p["gap_th"]).fillna(False)

    # activity_raw = 0.4·ATR_Ratio + 0.4·Volume_Ratio + 0.2·|Gap_Pct|（个股原值加权）
    activity_raw = 0.4 * ratio + 0.4 * vol_ratio + 0.2 * gap.abs()
    out["ACTIVITY_RAW"] = activity_raw
    out["IS_ACTIVE"] = (activity_raw >= p["activity_th"]).fillna(False)
    return out


def cross_section_activity_score(dfs: dict, params: dict | None = None) -> dict:
    """横截面活跃度评分注入（z 标准化加权）。

    输入：{'symbol': df}，df 需包含 trade_date 与 ATR_RATIO/VOLUME_RATIO/GAP_PCT（由
    calc_breitstein 或 _activity_layer 产出）。对每个交易日，在全 universe 上对三个分量的
    zscore 加权合成 Activity_Score 并写回各 df。返回新的 dict。
    """
    if not dfs:
        return dfs
    p = _merge_params(params)
    out_dfs = {}
    # 收集所有交易日
    all_dates = sorted({
        d for df in dfs.values()
        for d in _date_series(df)
    })
    # 建索引：symbol -> (date -> row)
    # 先用最后遇到的占比；把每个 df 的日期转成 Datetime 并取各日期的行
    symbols = list(dfs.keys())
    import collections
    rows_by = {}  # (date, sym) -> (atr, vol, gap)
    for sym, df in dfs.items():
        dates = _date_series(df)
        vals = df[["ATR_RATIO", "VOLUME_RATIO", "GAP_PCT"]].to_numpy()
        for i, d in enumerate(dates):
            rows_by[(pd.Timestamp(d), sym)] = vals[i]

    scores = {sym: np.full(len(dfs[sym]), np.nan) for sym in symbols}
    date_to_ser = {}
    for d in all_dates:
        d = pd.Timestamp(d)
        arr = []
        for sym in symbols:
            k = (d, sym)
            if k in rows_by:
                arr.append((sym, *rows_by[k]))
            else:
                arr.append((sym, np.nan, np.nan, np.nan))
        atr = np.array([a[1] for a in arr], dtype=float)
        vol = np.array([a[2] for a in arr], dtype=float)
        gap = np.abs(np.array([a[3] for a in arr], dtype=float))
        valid = ~(np.isnan(atr) & np.isnan(vol) & np.isnan(gap))
        z = {}
        for name, v in (("atr", atr), ("vol", vol), ("gap", gap)):
            vv = v.copy()
            m = vv[valid]
            if len(m) == 0 or m.std() == 0 or np.isnan(m.std()):
                zv = np.zeros_like(vv)
            else:
                zv = np.full_like(vv, np.nan)
                zv[valid] = (vv[valid] - m.mean()) / m.std()
            z[name] = zv
        combined = 0.4 * z["atr"] + 0.4 * z["vol"] + 0.2 * z["gap"]
        for i, sym in enumerate(symbols):
            date_to_ser[(d, sym)] = combined[i]

    for sym, df in dfs.items():
        dates = _date_series(df)
        vals = np.full(len(df), np.nan)
        for i, d in enumerate(dates):
            vals[i] = date_to_ser.get((pd.Timestamp(d), sym), np.nan)
        out_dfs[sym] = df.copy()
        out_dfs[sym]["ACTIVITY_SCORE"] = vals
    return out_dfs


def topn_universe(activity_scores: dict, n: int | None = None, cap: int | None = None) -> list:
    """按横截面 Activity_Score 对宇宙排名，返回前 topN（上限 cap）的 symbol 列表。

    activity_scores: {'symbol': score(float 或 NaN)}（单日），NaN 不计入。
    """
    p = default_params()
    n = p["topn"] if n is None else int(n)
    cap = p["topn_cap"] if cap is None else int(cap)
    n = min(n, cap)
    scored = [(s, sc) for s, sc in activity_scores.items()
              if sc is not None and not (isinstance(sc, float) and np.isnan(sc))]
    scored.sort(key=lambda x: (-float(x[1]), x[0]))
    return [s for s, _ in scored[:n]]


# ---------------------------------------------------------------------------
# 要点二：干净价位层
# ---------------------------------------------------------------------------

def _clean_level_layer(out: pd.DataFrame, p: dict) -> pd.DataFrame:
    n = len(out)
    high = out["high"]
    low = out["low"]
    close = out["close"]

    # 枢轴点识别（局部高/低，order 根，边界未确认位不用于触发）
    h_piv = _find_extrema(high.to_numpy(), p["pivot_order"])
    l_piv = _find_extrema(low.to_numpy(), p["pivot_order"])
    res_cands = [(i, v) for i, v, k, cf in h_piv if k == "H"]
    sup_cands = [(i, v) for i, v, k, cf in l_piv if k == "L"]

    # 每根 K 线的干净价位：只取已确认枢轴（i+pivot_order<=t），找最近支撑/阻力
    pivot_h = np.full(n, np.nan)
    pivot_l = np.full(n, np.nan)
    for i, v in res_cands:
        pivot_h[i] = v
    for i, v in sup_cands:
        pivot_l[i] = v
    out["PIVOT_H"] = pivot_h
    out["PIVOT_L"] = pivot_l

    conf_s = np.full(n, np.nan)
    conf_r = np.full(n, np.nan)
    lev_s = np.full(n, np.nan)
    lev_r = np.full(n, np.nan)
    touch_s = np.zeros(n)
    touch_r = np.zeros(n)
    order = p["pivot_order"]
    tol = p["touch_tol"]
    cw = p["conf_window"]
    for t in range(n):
        # 已确认的支撑 / 阻力
        sup = [(i, v) for i, v in sup_cands if i + order <= t and v < close.iloc[t]]
        res = [(i, v) for i, v in res_cands if i + order <= t and v > close.iloc[t]]
        if sup:
            # 最近支撑：价格下方最近的已确认价位
            s_idx, s_val = max(sup, key=lambda x: x[1])
            lev_s[t] = s_val
            # 只用 [t-cw+1, t] 已发生区间，避免前视
            tp, hp, cf = _level_stats(s_val, "S", high, low, close, tol, cw, t)
            conf_s[t] = cf
            touch_s[t] = tp * cw
        if res:
            r_idx, r_val = min(res, key=lambda x: x[1])
            lev_r[t] = r_val
            tp, hp, cf = _level_stats(r_val, "R", high, low, close, tol, cw, t)
            conf_r[t] = cf
            touch_r[t] = tp * cw

    out["LEVEL_SUPPORT"] = lev_s
    out["LEVEL_RESISTANCE"] = lev_r
    out["LEVEL_CONF_S"] = conf_s
    out["LEVEL_CONF_R"] = conf_r
    out["CLEAN_LEVEL"] = pd.Series((conf_s > p["conf_th"]) | (conf_r > p["conf_th"]), index=out.index).fillna(False)
    out["TOUCHES_S"] = touch_s
    out["TOUCHES_R"] = touch_r

    # 突破速度 ROC = (Close[t] − Close[t−N])/Close[t−N]/N（因果，衡量近期速度）
    rn = p["roc_n"]
    roc = (close - close.shift(rn)) / close.shift(rn) / rn
    out["BREAKOUT_ROC"] = roc

    # 锚定 VWAP / AVWAP：TP=(h+l+c)/3，avwap=cumsum(TP·Vol)/cumsum(Vol)
    tp = (high + low + close) / 3.0
    cvol = out["volume"].cumsum()
    avwap = (tp * out["volume"]).cumsum() / cvol
    # range==0 或量累计为 0 → NaN（越界安全）
    rng = high.rolling(p["comp_window"]).max() - low.rolling(p["comp_window"]).min()
    avwap = avwap.mask((rng == 0) | (cvol == 0))
    out["AVWAP"] = avwap
    out["AVWAP_SIDE"] = np.sign(close - avwap).fillna(0.0)

    out.attrs["pivots"] = {"high": res_cands, "low": sup_cands}
    return out


# ---------------------------------------------------------------------------
# 要点三：盘整层
# ---------------------------------------------------------------------------

def _consolidation_layer(out: pd.DataFrame, p: dict) -> pd.DataFrame:
    n = len(out)
    high, low, close, vol = out["high"], out["low"], out["close"], out["volume"]
    cn = p["comp_window"]

    # 区间压缩 range_N = rolling(N).max − rolling(N).min
    range_n = high.rolling(cn).max() - low.rolling(cn).min()
    avail = range_n.notna().sum()
    L = _hist_window(int(avail), p)
    pct = _rolling_pct_rank(range_n, L)
    mean_ = range_n.rolling(L, min_periods=max(6, L // 4)).mean()
    std_ = range_n.rolling(L, min_periods=max(6, L // 4)).std()
    z = (range_n - mean_) / std_
    out["RANGE_N"] = range_n
    out["RANGE_PCT"] = pct
    out["RANGE_Z"] = z
    out["COMPRESSED"] = ((pct < p["pct_th"]) | (z < p["z_th"])).fillna(False)

    # 净漂移：abs(Close−Close[N])/range_N；range_N==0 → 0
    drift = (close - close.shift(cn)).abs() / range_n
    drift = drift.mask(range_n == 0, 0.0)
    out["NET_DRIFT"] = drift
    drift_ok = (drift < p["drift_th"]).fillna(False)

    # 回归斜率：np.polyfit deg=1，abs(slope)/mean(close[-N:]) < 0.001
    reg_slope = np.full(n, np.nan)
    for t in range(cn - 1, n):
        w = close.iloc[t - cn + 1:t + 1].to_numpy(dtype=float)
        if len(w) < 3 or np.isnan(w).any() or w.mean() == 0:
            continue
        x = np.arange(len(w))
        slope = float(np.polyfit(x, w, 1)[0])
        reg_slope[t] = abs(slope) / abs(w.mean())
    out["REG_SLOPE"] = reg_slope
    slope_ok = pd.Series(reg_slope < p["slope_th"], index=out.index).fillna(False)

    # 区间均匀度 cv = daily_ranges[-N:].std/mean；mean==0 → NaN；CV<0.5 且单日≤箱体50%
    daily_rng = high - low
    cv = np.full(n, np.nan)
    cv_day_ok = np.zeros(n, dtype=bool)
    for t in range(cn - 1, n):
        w = daily_rng.iloc[t - cn + 1:t + 1].to_numpy(dtype=float)
        rn = range_n.iloc[t]
        if len(w) == 0 or np.isnan(rn):
            continue
        if np.isnan(w).any() or w.mean() == 0:
            cv[t] = np.nan
        else:
            cv[t] = w.std() / w.mean()
        if not np.isnan(rn) and rn > 0:
            cv_day_ok[t] = bool((w <= rn * p["cv_day_ratio"]).all())
    out["CV"] = cv
    cv_ok = (pd.Series(cv < p["cv_th"], index=out.index)
             & pd.Series(cv_day_ok, index=out.index)).fillna(False)
    out["UNIFORM"] = cv_ok

    # 量价结构：量能递减 slope(polyfit(range(N), vol[-N:],1))<0
    vol_slope = np.full(n, np.nan)
    for t in range(cn - 1, n):
        w = vol.iloc[t - cn + 1:t + 1].to_numpy(dtype=float)
        if len(w) < 3 or np.isnan(w).any():
            continue
        x = np.arange(len(w))
        vol_slope[t] = float(np.polyfit(x, w, 1)[0])
    out["VOLUME_SLOPE"] = vol_slope
    vol_ok = pd.Series(vol_slope < 0, index=out.index).fillna(False)
    out["VOLUME_TREND_OK"] = vol_ok

    out["CONSOLIDATED"] = (compressed_bool(out) & drift_ok & slope_ok & cv_ok & vol_ok)
    return out


def compressed_bool(out: pd.DataFrame) -> pd.Series:
    return out["COMPRESSED"]


# ---------------------------------------------------------------------------
# 要点四：情绪层与入场确认
# ---------------------------------------------------------------------------

def _emotion_layer(out: pd.DataFrame, p: dict) -> pd.DataFrame:
    close = out["close"]
    open_ = out["open"]
    vol = out["volume"]
    high = out["high"]
    low = out["low"]
    bb = p["bb_window"]

    # 布林带极端偏离
    sma = close.rolling(bb, min_periods=bb).mean()
    std = close.rolling(bb, min_periods=bb).std()
    bb_z = (close - sma).div(2 * std)
    out["BB_Z"] = bb_z
    out["EMOTION_EXTREME"] = (bb_z.abs() > p["bb_z_th"]).fillna(False)

    # 量能情绪
    vol_sma = vol.rolling(bb, min_periods=bb).mean()
    vol_std = vol.rolling(bb, min_periods=bb).std()
    volume_z = (vol - vol_sma) / vol_std
    direction = np.where(close > open_, 1.0, np.where(close < open_, -1.0, 0.0))
    sentiment = volume_z * direction
    out["VOLUME_Z"] = volume_z
    out["SENTIMENT_SCORE"] = sentiment
    out["SENTIMENT_EXTREME"] = (sentiment.abs() > p["sentiment_th"]).fillna(False)

    # 加速度
    velocity = close.diff()
    acceleration = velocity.diff()
    out["VELOCITY"] = velocity
    out["ACCELERATION"] = acceleration
    acc_mean = acceleration.rolling(p["accel_window"], min_periods=p["accel_window"]).mean()
    acc_std = acceleration.rolling(p["accel_window"], min_periods=p["accel_window"]).std()
    acc_z = (acceleration - acc_mean) / acc_std
    acc_z = acc_z.mask(acc_std.isna() | (acc_std == 0), 0.0)
    out["ACCEL_Z"] = acc_z
    out["SPEEDING_DOWN"] = ((velocity < 0) & (acceleration < 0)).fillna(False)

    # 连续同向 K 线（连续计数，非窗口和）
    is_down = (close < open_).astype(int)
    consec_grp = (~(is_down == 1)).cumsum()
    consec_down = is_down.groupby(consec_grp).cumsum()
    out["CONS_DOWN"] = consec_down

    # 综合情绪评分
    bb_a = bb_z.abs()
    sent_a = sentiment.abs()
    acc_a = acc_z.abs()
    emotion_score = 0.35 * bb_a.fillna(0.0) + 0.35 * sent_a.fillna(0.0) + 0.30 * acc_a.fillna(0.0)
    out["EMOTION_SCORE"] = emotion_score
    out["EMOTION_EXTREME_ZONE"] = (emotion_score > p["emotion_extreme_th"]).fillna(False)

    # 入场确认（投降 K 线 + 收复 AVWAP）
    # 投降 K 线：low<支撑 & close>支撑 & volume>2×SMA20
    support = out["LEVEL_SUPPORT"]
    cap_bar = (low < support) & (close > support) & (vol > p["capitulate_vol_mult"] * vol_sma)
    out["CAPITULATION_BAR"] = cap_bar.fillna(False)

    # 收复 AVWAP：AVWAP 锚点=投降 K 线当日开盘（最近投降 bar 起的 cumsum）；无则用整段 AVWAP
    tp = (high + low + close) / 3.0
    cum_tpv = (tp * vol).cumsum()
    cum_v = vol.cumsum()
    reclaim = pd.Series(False, index=out.index)
    lb = p["cap_anchor_lookback"]
    cap_idx = np.where(cap_bar.fillna(False).to_numpy())[0]
    for i, t in enumerate(out.index):
        j0 = max(0, i - lb + 1)
        # 最近投降 bar 索引（≤i 且在回看窗口内）
        cand = cap_idx[cap_idx <= i]
        if len(cand):
            j = int(cand[-1])
            if j >= j0:
                s = j
            else:
                s = 0
        else:
            s = 0
        ctv = cum_tpv.iloc[i] - (cum_tpv.iloc[s - 1] if s > 0 else 0)
        cvv = cum_v.iloc[i] - (cum_v.iloc[s - 1] if s > 0 else 0)
        if cvv <= 0 or np.isnan(cvv):
            continue
        av_anchor = ctv / cvv
        if close.iloc[i] > av_anchor:
            reclaim.loc[t] = True
    out["RECLAIM_AVWAP"] = reclaim.fillna(False)

    out["ENTRY_REVERSAL"] = (cap_bar & reclaim).fillna(False)
    return out


# ---------------------------------------------------------------------------
# 要点五：多周期共振层
# ---------------------------------------------------------------------------

def _resonance_layer(out: pd.DataFrame, p: dict) -> pd.DataFrame:
    dates = _date_series(out)
    close = out["close"]
    high = out["high"]
    n = len(out)

    # 日线趋势
    daily_dir = _trend_dir(close.reset_index(drop=True), p).to_numpy()

    # 周线 / 月线趋势（仅已结束周期）
    weekly_dir = _resampled_trend(dates, close, "W-FRI", p)
    monthly_dir = _resampled_trend(dates, close, "M", p)
    wk = _asof_trend(dates, weekly_dir)
    mo = _asof_trend(dates, monthly_dir)
    w_d = wk.to_numpy()
    m_d = mo.to_numpy()

    out["DAILY_DIR"] = daily_dir
    out["WEEKLY_DIR"] = w_d
    out["MONTHLY_DIR"] = m_d

    # 共振评分：只累加方向非 0 的周期
    res = np.zeros(n)
    cnt = np.zeros(n)
    for i in range(n):
        parts = [daily_dir[i], w_d[i], m_d[i]]
        nz = [x for x in parts if x != 0]
        res[i] = sum(nz)
        cnt[i] = len(nz)
    out["RESONANCE"] = res
    out["RESONANCE_COUNT"] = cnt

    # 仓位乘数 = 1 + 0.25×（resonance/实际计入周期数）
    mult = np.full(n, 1.0)
    if np.isfinite(res).all():
        safe = np.zeros(n)
        np.divide(res, cnt, out=safe, where=cnt > 0)
        mult = np.where(cnt > 0, 1.0 + 0.25 * safe, 1.0)
    out["POSITION_MULT"] = mult

    # 关键位重合：close 与 日线 roll20 高、周线 roll4 高 距离 <0.5%
    dh = high.rolling(p["daily_res_window"], min_periods=1).max()
    # 周线高 roll4
    tmp = pd.DataFrame({"date": pd.DatetimeIndex(dates),
                        "high": high.to_numpy()})
    per = tmp["date"].dt.to_period("W-FRI")
    wg = tmp.groupby(per)["high"].max()
    wg4 = wg.rolling(p["weekly_res_window"], min_periods=1).max()
    wg4.name = "wh"
    wgframe = pd.DataFrame({"date": pd.DatetimeIndex(wg4.index.to_timestamp(how="end")).as_unit('ns'),
                            "wh": wg4.to_numpy()})
    left = pd.DataFrame({"date": pd.DatetimeIndex(dates).as_unit('ns')}).reset_index()
    mm = pd.merge_asof(left, wgframe, on="date", direction="backward")
    wh = mm["wh"].to_numpy()

    d_close = close.to_numpy()
    ol = np.zeros(n, dtype=bool)
    for i in range(n):
        c = d_close[i]
        if np.isnan(c):
            continue
        if not np.isnan(dh.iloc[i]) and abs(c - dh.iloc[i]) <= p["res_overlap_tol"] * dh.iloc[i]:
            ol[i] = True
        if not np.isnan(wh[i]) and abs(c - wh[i]) <= p["res_overlap_tol"] * wh[i]:
            ol[i] = True
    out["LEVEL_OVERLAP"] = ol
    return out


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def calc_breitstein(df: pd.DataFrame, params: dict | None = None,
                    benchmark: pd.DataFrame | None = None) -> pd.DataFrame:
    """计算 Breitstein 五要点指标，返回带全部字段的新 DataFrame。

    保留原 trade_date / is_st / limit_up 等列；benchmark 保留为 API 占位（本期未使用）。
    """
    p = _merge_params(params)
    result = df.copy()
    result = _activity_layer(result, p)
    result = _clean_level_layer(result, p)
    result = _consolidation_layer(result, p)
    result = _emotion_layer(result, p)
    result = _resonance_layer(result, p)
    result.attrs["breitstein_params"] = p
    return result


# ---------------------------------------------------------------------------
# 综合筛选框架（breitstein_screen）：双管道并行
# ---------------------------------------------------------------------------

def breitstein_screen(symbol, daily_df, params: dict | None = None,
                      weekly_df: pd.DataFrame | None = None):
    """双管道筛选：A 盘整突破型（不要求极端情绪）/ B 情绪反转型（不要求盘整）。

    在 daily_df（需含信号所需日线数据）基础上计算指标，取最后一根 K 线评估。
    任一管道缺项返回对应 None 结果；两管道互不拦截。
    返回 (breakout_dict, reversal_dict)，dict 字段与 spec FR-6 一致。
    """
    p = _merge_params(params)
    ind = calc_breitstein(daily_df, p, None)
    row = ind.iloc[-1]

    def _base():
        return {
            "symbol": symbol,
            "activity_score": float(row.get("ACTIVITY_RAW", np.nan))
            if np.isfinite(row.get("ACTIVITY_RAW", np.nan)) else None,
            "clean_levels": [],
            "compression": None,
            "emotion_score": float(row.get("EMOTION_SCORE", np.nan)),
            "resonance_score": float(row.get("RESONANCE", 0.0)),
            "position_multiplier": float(row.get("POSITION_MULT", 1.0)),
        }

    # ---- 管道 A：盘整突破型 ----
    # 语义（FR-6/FR-7）：市场此前处于箱体盘整（压缩），当日收盘有效突破箱顶
    # （干净阻力位）且 ROC 达标。因突破后 close 已远超旧阻力位，"LEVEL_RESISTANCE"
    # （恒在 close 上方）不再适用，故以"近期箱顶 = 被突破的干净阻力位"评估，
    # 压缩态取突破前一根（箱体尚未被放大）的 RANGE_PCT / RANGE_Z。
    breakout = None
    act = row.get("ACTIVITY_RAW")
    roc = row.get("BREAKOUT_ROC")
    hi = ind["high"]
    lo = ind["low"]
    cl = ind["close"]
    cn = p["comp_window"]
    box_hi = hi.rolling(cn).max().shift(1)   # 前一根已定箱顶（因果，不含当日突破柱）
    box_hi_v = box_hi.iloc[-1]
    cp_prev = ind["RANGE_PCT"].shift(1).iloc[-1]
    cz_prev = ind["RANGE_Z"].shift(1).iloc[-1]
    compressed_prior = bool(
        (np.isfinite(cp_prev) and cp_prev < p["pct_th"])
        or (np.isfinite(cz_prev) and cz_prev < p["z_th"])
    )
    if (act is not None and np.isfinite(act) and act >= p["activity_th"]
            and np.isfinite(box_hi_v)
            and row["close"] > box_hi_v
            and roc is not None and np.isfinite(roc) and roc > p["roc_min"]
            and compressed_prior):
        # 箱顶曾在滞留期（close<箱顶）被多次触及 → 干净的已突破阻力位
        cl_n = cl.to_numpy()
        hi_n = hi.to_numpy()
        mv = cl_n < box_hi_v
        seg_hi = hi_n[mv]
        seg_cl = cl_n[mv]
        w_lv = min(p["conf_window"], int(mv.sum()))
        if w_lv > 0 and len(seg_hi) >= w_lv:
            s_hi = seg_hi[-w_lv:]
            s_cl = seg_cl[-w_lv:]
            touch = (s_hi >= box_hi_v * (1 - p["touch_tol"])) & (s_cl < box_hi_v)
            conf_lv = int(touch.sum()) / w_lv
        else:
            conf_lv = 0.0
        if conf_lv > p["conf_th"]:
            b = _base()
            b["opportunity"] = "breakout"
            b["clean_levels"] = [{"price": float(box_hi_v),
                                  "kind": "R",
                                  "confidence": float(conf_lv)}]
            b["compression"] = {
                "percentile_rank": float(cp_prev) if np.isfinite(cp_prev) else np.nan,
                "z_score": float(cz_prev) if np.isfinite(cz_prev) else np.nan,
            }
            breakout = b

    # ---- 管道 B：情绪反转型 ----
    reversal = None
    conf_s = row.get("LEVEL_CONF_S")
    emotion = row.get("EMOTION_SCORE")
    entry = bool(row.get("ENTRY_REVERSAL", False))
    sup = row.get("LEVEL_SUPPORT")
    if (act is not None and not np.isnan(act) and act >= p["activity_th"]):
        if conf_s is not None and np.isfinite(conf_s) and conf_s > p["conf_th"]:
            if emotion is not None and np.isfinite(emotion) and abs(emotion) >= p["pipeline_b_emotion_th"]:
                if entry:
                    r = _base()
                    r["opportunity"] = "reversal"
                    r["clean_levels"] = [{"price": float(sup) if sup is not None and np.isfinite(sup) else np.nan,
                                          "kind": "S",
                                          "confidence": float(conf_s)}]
                    r["compression"] = {
                        "percentile_rank": float(row.get("RANGE_PCT", np.nan)),
                        "z_score": float(row.get("RANGE_Z", np.nan)),
                    }
                    reversal = r

    return breakout, reversal