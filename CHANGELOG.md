# 更新日志 / Changelog

本平台采用 Semantic Versioning（语义化版本）。每次更新递增版本号，并在本文件记录
主要变更。

## 1.0.1（当前正式版）

**功能**
- 完整实现 Lance Breitstein「支撑阻力五要点」量化为五层筛选：活跃度（Activity）→
  干净价位（Clean Level）→ 适当盘整（Consolidation）→ 情绪反应（Emotion）→
  多周期一致（Resonance）。
- 综合双管道筛选（`breitstein_screen`）：管道 A（盘整突破）/ 管道 B（情绪反转）/
  A+B 并行（默认）。
- 出场保护优先级链：固定止损 > 结构/AVWAP > ATR > 共振走弱 > 活跃衰减 > 到期。
- 三种数据源：API（默认，本地计算指标）、直连 MySQL、本地 CSV 导入（完全离线）。
- A 股约束：涨跌停 / ST / 最小上市天数 / 成本与滑点。
- 本地行情缓存（月片 / 面板），可选磁盘持久化，加密绑定当前用户。

**分发 / 运行**
- 便携 Python 3.12 随包附带；源码包不携带运行时，首次启动自动下载 / 校验 / 解压
  （断点续传 + 重试），或按 README 手动下载兜底。
- 多平台发布：gitcode、GitHub、gitee（自动下载主源为 gitcode 发行版）。

**已知限制**
- 仅支持 Windows（便携运行时与启动脚本均为 Windows 版本）。
- 缓存绑定当前 Windows 用户，跨机器迁移需重新取数、重建缓存。
- 直连 MySQL 的 `sql_market.py` 为本地文件、不入库，仅提供模板 `sql_market.example.py`。