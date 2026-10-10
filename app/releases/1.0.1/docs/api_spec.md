# rxlib 数据 API 接口与数据规范

本文档用于指导 rxlib 数据 API 的开发。API 只提供回测所需的只读市场数据，不提供下单、账户、生产信号或交易执行能力。

## 1. 设计目标

客户端通过统一的 `DataProvider` 抽象访问数据：

| 客户端方法 | 对应接口 | 用途 |
| --- | --- | --- |
| `load_universe(user_id)` | `GET /api/v1/universe` | 获取全市场、用户自选股或指定代码的股票基础资料 |
| `load_kline(codes, start, end)` | `GET /api/v1/kline` | 获取股票日 K 数据，服务端自动返回指标预热数据 |
| `benchmark(code, start, end)` | `GET /api/v1/benchmark` | 获取基准指数收盘价 |
| `latest_trade_date()` | `GET /api/v1/latest-trade-date` | 获取服务端可用的最新交易日 |
| 交易日历查询 | `GET /api/v1/trade-cal` | 获取交易日历，建议提供 |

API 返回的数据结构应与当前 MySQL 数据提供层一致，使客户端仅替换数据提供实例即可切换数据源。

## 2. 通用约定

### 2.1 基础地址

统一使用版本化路径：

```text
http://<host>:8508/api/v1
```

本地默认服务仅绑定回环地址：

```text
http://127.0.0.1:8508/api/v1
```

示例：

```text
http://127.0.0.1:8508/api/v1/universe
http://127.0.0.1:8508/api/v1/kline
```

### 2.2 鉴权

除健康检查外，所有接口均应校验访问令牌：

```http
Authorization: Bearer <token>
X-Rxlib-Client: <client_id>
X-Rxlib-Version: rxlib/<version>
```

要求：

- Token 不在 URL query 中传递。
- Token 必须服务端可撤销或设置有效期。
- 建议启用 HTTPS。
- 鉴权失败返回 HTTP 401。
- Token 有效但无权限返回 HTTP 403。

客户端实例标识（用于设备识别与风控，服务端默认只记录、不阻断）：

- `X-Rxlib-Client`：本机实例 UUID。首次运行生成并持久化到 `~/.rxlib/client_id`，之后每次启动复用，跨进程/重启保持稳定；不做定时心跳，仅随真实业务请求上报。
- `X-Rxlib-Version`：客户端版本，如 `rxlib/1.0.1`，便于服务端统计与灰度。
- 设备被管理员停用后，该 UUID 的请求返回 HTTP 403。

### 2.3 通用响应结构

成功响应：

```json
{
  "code": 0,
  "msg": "ok",
  "data": {}
}
```

失败响应：

```json
{
  "code": 40001,
  "msg": "codes 参数格式错误",
  "data": null
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | integer | `0` 表示业务成功，非 `0` 表示业务失败 |
| `msg` | string | 错误说明或成功描述 |
| `data` | object / array / null | 业务数据 |

HTTP 状态码用于表示请求处理状态；`code` 用于表示业务状态。HTTP 非 2xx 或 `code != 0` 时，客户端均应视为失败。

### 2.4 数据格式约定

| 项目 | 约定 |
| --- | --- |
| 日期格式 | `YYYY-MM-DD`，例如 `2026-09-18` |
| 时间范围 | 闭区间，包含 `start` 和 `end` |
| 唯一标识 `rxcode` | 交易所前置，格式 `<交易所>.<6位码>`，例如 `SZ.000001`、`SH.600000`，全系统唯一，是分组、匹配、缓存键的唯一依据 |
| 股票代码 `code` | 6 位字符串，不带交易所后缀，例如 `600000`，仅用于展示与便捷入参；6 位码可能重复，不得作为唯一键 |
| 指数代码 | 6 位字符串，例如 `000300`、`399006` |
| 价格字段 | JSON number，单位与当前行情库一致 |
| 成交量 | JSON number，单位与当前行情库一致 |
| 空值 | `null` |
| 布尔字段 | `0` / `1` |
| 字符编码 | UTF-8 |
| 排序 | `rxcode`、交易日期均按升序返回 |

当前回测使用未复权原始价格。API 如增加复权能力，应通过明确参数声明，不得静默改变价格口径。

### 2.5 HTTP 与性能要求

- 所有数据查询接口必须是安全、只读、可重复调用的。
- 建议支持 gzip 响应压缩。
- 建议添加缓存响应头和 ETag。
- 建议设置接口限流。
- 单次请求代码数量建议不超过 1000 个。
- 全市场 K 线（不传 `codes`）必须带 `month` 参数（`YYYY-MM`）按月分片请求，避免单次响应过大；指定月份的月表不存在或当月无行情时，`rows` 返回空数组 `[]`，不返回错误。
- 如代码列表过长，可追加等价的 POST 查询接口，但 GET 接口必须先满足常规使用。

## 3. 错误码

### 3.1 HTTP 状态码

| 状态码 | 含义 |
| --- | --- |
| 200 | 请求成功 |
| 400 | 请求参数错误 |
| 401 | 未认证或令牌无效 |
| 403 | 已认证但无访问权限 |
| 404 | 接口不存在 |
| 429 | 请求过于频繁 |
| 500 | 服务端内部错误 |
| 502 | 上游数据源错误 |
| 503 | 服务暂不可用 |
| 504 | 获取上游数据超时 |

### 3.2 业务错误码

| code | 含义 |
| --- | --- |
| 0 | 成功 |
| 40001 | 参数格式错误 |
| 40002 | 日期区间错误 |
| 40003 | 代码格式错误 |
| 40101 | Token 缺失 |
| 40102 | Token 已失效 |
| 40301 | 无该数据权限 |
| 40401 | 数据不存在 |
| 42901 | 触发限流 |
| 50001 | 服务内部错误 |
| 50201 | 上游数据库或行情源错误 |
| 50301 | 数据服务暂不可用 |

## 4. 股票池接口

### 4.1 获取股票池

```http
GET /api/v1/universe
```

### 4.2 请求参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` | integer | 否 | 用户 ID。传入时返回该用户的自选股；不传时返回全市场 |
| `codes` | string | 否 | 逗号分隔的 6 位股票代码，用于查询指定股票 |
| `type` | integer | 否 | 证券类型，默认 `1`，表示 A 股股票 |
| `active` | integer | 否 | 是否只返回有效股票，默认 `1` |

规则：

- `user_id` 与 `codes` 同时传入时，以 `codes` 为准。
- 不传 `user_id` 和 `codes` 时，返回服务端定义的全 A 市场。
- `user_id` 必须为正整数。
- `codes` 示例：`600000,000001,300750`。
- 服务端只返回有效股票；停牌但仍上市的股票仍可返回。

### 4.3 响应数据

`data` 为对象：

```json
{
  "st": ["SZ.000021", "SH.600225"],
  "rows": [
    {
      "rxcode": "SH.600000",
      "code": "600000",
      "name": "浦发银行",
      "list_date": "1999-11-10",
      "is_st": 0
    },
    {
      "rxcode": "SZ.000021",
      "code": "000021",
      "name": "ST某公司",
      "list_date": "1994-05-01",
      "is_st": 1
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `st` | array[string] | 当前股票池中 ST 股票的 `rxcode` 列表（唯一） |
| `rows` | array[object] | 股票基础资料列表 |
| `rows[].rxcode` | string | 交易所前置唯一标识，如 `SH.600000` |
| `rows[].code` | string | 6 位股票代码，仅用于展示，可能重复 |
| `rows[].name` | string | 证券简称 |
| `rows[].list_date` | string / null | 上市日期 |
| `rows[].is_st` | integer | 是否处于 ST 或风险警示状态，`1` 表示是 |

### 4.4 当前 MySQL 对应关系

| API 字段 | 当前数据来源 |
| --- | --- |
| `rxcode` | 由库内 `full_code`（`000001.SZ`）在 SQL 内换算为交易所前置（`SZ.000001`） |
| `code` | 由库内 `full_code` 在 SQL 内取 `.` 前缀的 6 位码 |
| `name` | `stock_base.name` |
| `list_date` | `stock_base.list_date` |
| `is_st` | `stock_base.is_st` |

用户自选股来源：

```text
rxdata_dev.user_favorites
rxdata_dev.user_favorites.user_id
rxdata_dev.user_favorites.stock_id
```

全市场过滤条件：

- `stock_base.type = 1`
- `stock_base.is_active = 1`
- 代码前缀属于当前支持的 A 股板块前缀。

## 5. 股票日 K 接口

### 5.1 获取股票日 K

```http
GET /api/v1/kline
```

### 5.2 请求参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `codes` | string | 否 | 逗号分隔的 6 位股票代码 |
| `start` | string | 是 | 回测开始日期，格式 `YYYY-MM-DD` |
| `end` | string | 是 | 回测结束日期，格式 `YYYY-MM-DD` |
| `month` | string | 否 | 全市场按月分片查询时必填，格式 `YYYY-MM`；仅在不传 `codes` 时使用 |
| `adjust` | string | 否 | 复权类型，默认 `none`；可扩展为 `qfq`、`hfq` |

规则：

- `start` 不得晚于 `end`。
- `codes` 为空时，表示全市场查询，此时必须提供 `month`，只返回该月数据，不做预热。
- 指定 `codes` 时，不存在的代码不影响其他代码返回；未命中的唯一标识在 `not_found` 中返回。
- 指定 `codes` 时服务端自动向前返回预热数据，不要求客户端额外传参。
- 指定 `codes` 时预热下限为 `start - 60 个自然日`，返回区间为 `[start - 60 自然日, end]`。
- 停牌日不构造空 K 线。
- 非交易日不返回数据。

预热数据用于计算 60 日均量、ATR20、MA60 主趋势与 60 日相对强度 RS 等威科夫特征。

### 5.3 响应数据

`data` 为对象：

```json
{
  "start": "2026-03-01",
  "end": "2026-09-18",
  "warmup_start": "2025-12-31",
  "adjust": "none",
  "not_found": [],
  "rows": [
    {
      "rxcode": "SH.600000",
      "code": "600000",
      "name": "浦发银行",
      "trade_date": "2025-12-31",
      "open": 8.1,
      "high": 8.3,
      "low": 8.0,
      "close": 8.2,
      "volume": 12345600,
      "limit_up": 8.91,
      "is_st": 0
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `start` | string | 请求的回测开始日期 |
| `end` | string | 请求的结束日期 |
| `warmup_start` | string | 服务端实际使用的预热开始日期 |
| `adjust` | string | 实际复权类型 |
| `not_found` | array[string] | 未找到数据的 `rxcode` 列表（唯一、去重） |
| `rows` | array[object] | 日 K 数据；全市场按月查询时月表为空则为 `[]` |
| `rows[].rxcode` | string | 交易所前置唯一标识，如 `SH.600000` |
| `rows[].code` | string | 6 位股票代码，仅用于展示，可能重复 |
| `rows[].name` | string | 证券简称 |
| `rows[].trade_date` | string | 交易日期 |
| `rows[].open` | number / null | 开盘价 |
| `rows[].high` | number / null | 最高价 |
| `rows[].low` | number / null | 最低价 |
| `rows[].close` | number / null | 收盘价 |
| `rows[].volume` | number | 成交量，无数据时为 `0` |
| `rows[].limit_up` | number / null | 当日涨停价 |
| `rows[].is_st` | integer | 当日是否为 ST，`1` 表示是 |

排序要求：

1. `rxcode` 升序。
2. 同一 `rxcode` 内 `trade_date` 升序。

### 5.4 当前 MySQL 对应关系

| API 字段 | 当前数据来源 |
| --- | --- |
| `rxcode` | 由库内 `full_code` 在 SQL 内换算为交易所前置 |
| `code` | 由库内 `full_code` 在 SQL 内取 6 位码 |
| `name` | `name` |
| `trade_date` | `trade_date` |
| `open` | `open_price` |
| `high` | `high_price` |
| `low` | `low_price` |
| `close` | `close_price` |
| `volume` | `volume` |
| `limit_up` | `limit_up_price` |
| `is_st` | `is_st` |

当前月度行情表命名：

```text
stock_daily_YYYYMM
```

服务端 API 不应向客户端暴露具体月表和分片实现。

## 6. 基准指数接口

### 6.1 获取基准指数

```http
GET /api/v1/benchmark
```

### 6.2 请求参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `code` | string | 否 | 指数代码，默认 `000300` |
| `start` | string | 是 | 开始日期 |
| `end` | string | 是 | 结束日期 |

规则：

- 默认基准为沪深 300：`000300`。
- `start` 不得晚于 `end`。
- 服务端负责识别指数代码对应的交易所后缀。
- 基准接口不需要额外预热数据。
- 无基准数据时返回空数组，不使用错误响应。

### 6.3 响应数据

```json
{
  "code": "000300",
  "rxcode": "SH.000300",
  "rows": [
    {
      "trade_date": "2026-03-02",
      "close": 4052.31
    },
    {
      "trade_date": "2026-03-03",
      "close": 4068.9
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `code` | string | 实际查询的 6 位指数代码 |
| `rxcode` | string | 交易所前置唯一标识，如 `SH.000300` |
| `rows` | array[object] | 指数日线数据 |
| `rows[].trade_date` | string | 交易日期 |
| `rows[].close` | number / null | 指数收盘点位 |

### 6.4 当前 MySQL 对应关系

指数代码后缀规则：

| 代码前缀 | 交易所后缀 |
| --- | --- |
| `399` | `.SZ` |
| `000`、`950` | `.SH` |

服务端从月度行情表按库内主键 `full_code`（仅出现在 SQL 中）定位，换算出 `rxcode`，对外只暴露 `rxcode`、`trade_date`、`close`。

客户端将返回结果转换为以 `trade_date` 为索引、`close` 为值的时间序列。

## 7. 最新交易日接口

### 7.1 获取最新交易日

```http
GET /api/v1/latest-trade-date
```

### 7.2 请求参数

无业务参数。

可通过以下参数控制数据口径：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `market` | string | 否 | 市场代码，默认返回 A 股口径 |

### 7.3 响应数据

```json
{
  "trade_date": "2026-09-18"
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `trade_date` | string | 服务端已落地行情数据中的最新交易日 |

规则：

- 返回的是“已有行情数据”的最新交易日，不是交易日历中的未来日期。
- 如果最新月表存在但尚无任何行情，应继续向前查找已有数据的月份。
- 当前 MySQL 实现通过查找 `stock_daily_YYYYMM` 月表中的最大 `trade_date` 得到。
- 找不到任何行情数据时返回业务错误 `40401`。

## 8. 交易日历接口

建议实现该接口，用于客户端在无行情日期、节假日或未来日期场景下判断交易日。

### 8.1 获取交易日历

```http
GET /api/v1/trade-cal
```

### 8.2 请求参数

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `start` | string | 是 | 开始日期 |
| `end` | string | 是 | 结束日期 |
| `exchange` | string | 否 | 交易所代码，默认 `SSE`，可按统一口径返回 A 股交易日 |

### 8.3 响应数据

```json
{
  "exchange": "SSE",
  "rows": [
    {
      "trade_date": "2026-09-18",
      "is_open": 1
    },
    {
      "trade_date": "2026-09-19",
      "is_open": 0
    }
  ]
}
```

字段说明：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `exchange` | string | 交易所或市场口径 |
| `rows` | array[object] | 日历列表 |
| `rows[].trade_date` | string | 自然日日期 |
| `rows[].is_open` | integer | 是否为交易日，`1` 表示是 |

也可以只返回交易日数组：

```json
{
  "trade_dates": ["2026-09-18", "2026-09-21"]
}
```

为便于客户端直接识别周末和节假日，建议返回第一种完整结构。

## 9. 健康检查与元信息接口

### 9.1 健康检查

```http
GET /health
```

该接口不要求鉴权。

成功响应：

```json
{
  "status": "ok"
}
```

健康检查应至少覆盖：

- API 服务进程状态。
- 数据提供层连接状态。
- 必要的配置加载状态。

### 9.2 服务元信息

建议提供：

```http
GET /api/v1/meta
```

响应示例：

```json
{
  "api_version": "1.0.0",
  "data_version": "2026-09-18",
  "latest_trade_date": "2026-09-18",
  "supported_adjust": ["none"],
  "generated_at": "2026-09-18T16:30:00+08:00"
}
```

该接口用于客户端判断 API 能力、数据版本和行情更新状态。

## 10. 客户端配置建议

客户端至少需要以下配置：

```ini
[api]
base_url = http://127.0.0.1:8508/api/v1
token = <token>
timeout = 10
```

建议增加：

```ini
enabled = true
verify_ssl = true
```

要求：

- Token 不应写入结果文件、日志或错误信息。
- 配置缺失时应提示“未配置 API 地址或访问令牌”，而不是导入失败。
- 网络超时、HTTP 错误、业务错误应分别给出可理解的提示。

## 11. 最小交付范围

API 最小可接入范围为：

1. `GET /api/v1/universe`
2. `GET /api/v1/kline`
3. `GET /api/v1/benchmark`
4. `GET /api/v1/latest-trade-date`
5. `GET /health`

建议同步交付：

1. `GET /api/v1/trade-cal`
2. `GET /api/v1/meta`
3. gzip 压缩
4. ETag 或等效缓存机制
5. 接口限流与访问日志

## 12. 验收要点

- 股票代码始终为 6 位字符串，不能因 JSON 解析丢失前导零。
- `rxcode` 为交易所前置且全局唯一；`code` 允许重复，分组、匹配、去重必须使用 `rxcode`。
- `start` 至 `end` 为闭区间。
- 指定 `codes` 的 K 线必须包含 `start` 之前 60 个自然日的预热数据。
- K 线按 `rxcode`、`trade_date` 升序返回。
- 全市场月片查询必须带 `month`，空月表返回 `rows: []`。
- 价格和成交量口径与当前 MySQL 数据一致。
- `limit_up` 缺失时返回 `null`，不能填 0，避免被误判为真实涨停价。
- 无基准数据时返回空数组。
- 最新交易日必须来自已落地行情，不能只按自然日推算。
- 空股票池、空 K 线、空信号日和库内最后交易日均应正常响应。
- Token 错误、权限不足、参数错误、上游数据库故障均应有明确错误码。
- API 返回结果经客户端转换后，字段名称和数据类型应满足现有回测内核要求。

## 13. 当前回测内核字段需求汇总

### 13.1 股票基础资料必需字段

| 字段 | 必需性 | 用途 |
| --- | --- | --- |
| `rxcode` | 必需 | 股票唯一标识、信号与持仓匹配（`code` 可能重复，不可用于此用途） |
| `code` | 必需 | 6 位码展示 |
| `name` | 必需 | 交易明细和候选列表展示 |
| `list_date` | 必需 | 次新股过滤 |
| `is_st` | 必需 | ST 过滤和买入限制 |

### 13.2 日 K 必需字段

| 字段 | 必需性 | 用途 |
| --- | --- | --- |
| `rxcode` | 必需 | 股票匹配、面板分组与缓存键 |
| `code` | 建议每行返回 | 6 位码展示，可能重复 |
| `name` | 建议每行返回 | 展示和结果输出 |
| `trade_date` | 必需 | 交易日索引、信号和撮合 |
| `open` | 必需 | 次日开盘买入、开盘卖出 |
| `high` | 必需 | 技术指标、止盈、止损和高点判断 |
| `low` | 必需 | 技术指标、止损和低点判断 |
| `close` | 必需 | 指标计算、信号登记、收盘卖出和净值 |
| `volume` | 必需 | 成交量均线、放量和缩量判断 |
| `limit_up` | 必需 | 一字涨停买入限制 |
| `is_st` | 必需 | ST 股票买入限制 |

### 13.3 基准数据必需字段

| 字段 | 必需性 | 用途 |
| --- | --- | --- |
| `trade_date` | 必需 | 与回测交易日对齐 |
| `close` | 必需 | 基准净值、Alpha、Beta 和信息比率 |

### 13.4 最新交易日必需字段

| 字段 | 必需性 | 用途 |
| --- | --- | --- |
| `trade_date` | 必需 | 默认结束日期和数据新鲜度判断 |
