# rxlib 平台架构说明

本平台是一个**本地运行、纯离线优先**的 A 股量化选股 / 因子回测工具（Windows）。
本文面向检索者与二次开发者，说明整体构成与模块边界。

## 1. 启动链路

```
start.bat ──► run.ps1 ──► streamlit run app/current/factor_lab_ui.py
                 │
                 ├─ 缺失 app/current？→ 自动从 app/releases/ 复制最新版本（克隆即用）
                 ├─ 缺失运行时 runtime/<name>/python.exe？→ bootstrap_runtime.ps1 自动下载
                 └─ 服务就绪 → 自动打开浏览器 http://localhost:8504
```

- `start.bat`：Windows 双击入口，调用 `run.ps1`。
- `run.ps1`：主启动脚本。负责：定位版本目录（含 Junction 自修复）、校验 / 触发运行时
  下载、设置隔离依赖环境变量、以 `streamlit run` 启动服务、等待就绪并打开浏览器。
- `launcher.py`：无窗口常驻启动器（供任务计划 / 开机后台静默使用），环境变量
  `RXLIB_PORT` 可覆盖端口，日志写入 `logs/launcher-YYYYMMDD.log`。
- `app/current`：当前生效版本。在开发仓库里它是 NTFS Junction（指向
  `app/releases/{version}`），git 不跟踪；克隆后缺失时由 `run.ps1` 自动补齐。
- `app/releases/{version}/`：各历史版本发布目录，每个版本含自身 `manifest.json`。

## 2. 版本与运行时引导

- `manifest.json`：定义本版本运行时名（如 `python312`）、下载地址与 SHA-256 校验值。
- `runtime/python312/`：便携 Python 3.12 + 隔离依赖（pylibs），**不随代码仓库分发**。
- `bootstrap_runtime.ps1`：首次启动自动下载 / 校验 / 解压运行时（断点续传 + 最多 3 次重试）。
- `build_runtime.ps1`：将本地 `runtime/python312` 打包为发行 zip 并输出 SHA-256（离线构建用）。

## 3. 数据层

- `data_provider.py`：统一取数提供层。支持三种模式：
  - **API**（默认）：经 `httpx` 调 HTTP 接口取数，指标仍在本地计算，API 仅替代取数；
  - **直连 MySQL**：读取本地行情库，依赖 `sql_market.py`（本地文件，不入库，仅提供
    `sql_market.example.py` 模板）与 `sql_queries.py` / `sql_system.py`；
  - **本地 CSV 导入**：完全离线，从 `[import] data_dir` 读取按月文件 `daily_YYYYMM.csv`。
- `cache_main.py`：行情月片 / 面板缓存（磁盘持久化，绑定当前 Windows 用户，加密）。
- `cache_crypto.py`：缓存加解密，密钥与当前用户绑定，故缓存**不可跨机器迁移**。
- `sql_system.py` / `sql_queries.py`：系统表与行情查询 SQL 封装。

## 4. 策略层

- `rx_core.py`：底层行情 / 指标基础工具（numpy / pandas 实现）。
- `breitstein.py`：Lance Breitstein「支撑阻力五要点」的核心量化实现（活跃度 → 干净价位 →
  盘整 → 情绪 → 多周期共振），含综合双管道（A 突破 / B 反转）与出场优先级链。
- `factor_lab.py`：因子 / 回测计算引擎。
- `factor_lab_ui.py`：Streamlit 图形界面入口与交互层（选股、回测、设置、示例数据导入）。
- `local_settings.py` / `platform_env.py`：本地配置与环境信息（含 `[import] data_dir`
  的默认回落逻辑）。

## 5. 主要内置第三方依赖

依赖**随便携运行时内置**，无需 `pip install`。以下为源码中实际使用的库：

| 依赖        | 用途                                            |
| ---------- | --------------------------------------------- |
| `numpy`    | 数值计算 / 向量化指标                             |
| `pandas`   | 行情数据组织、回测与面板处理                        |
| `streamlit` | Web 图形界面（`streamlit run`）                 |
| `plotly`   | 交互式图表（K 线、收益曲线等）                      |
| `httpx`    | API 模式的 HTTP 取数客户端                        |

> 部署时，以上依赖已打包进 `runtime/python312/pylibs/`，因此用户拿到源码包后
> 首次启动自动下载运行时即可直接运行，无需任何手工安装依赖。