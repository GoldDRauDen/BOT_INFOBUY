-- Schema SQLite cho BOT_INFOBUY (phan tich dinh luong)
-- Don vi: gia VND, volume co phieu, ngay YYYY-MM-DD (gio VN UTC+7)

CREATE TABLE IF NOT EXISTS symbols (
    symbol TEXT PRIMARY KEY,
    organ_name TEXT,
    en_organ_name TEXT,
    exchange TEXT,
    sector TEXT,
    industry_code TEXT,
    is_active INTEGER DEFAULT 1,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS ohlcv_daily (
    symbol TEXT,
    trade_date TEXT,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume INTEGER,
    source TEXT,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS fundamentals_quarterly (
    symbol TEXT,
    quarter_end TEXT,
    reported_date TEXT,
    is_estimated INTEGER,
    eps REAL,
    pe_ratio REAL,
    pb_ratio REAL,
    roe REAL,
    roa REAL,
    bvps REAL,
    revenue REAL,
    net_income REAL,
    dividend_yield REAL,
    PRIMARY KEY (symbol, quarter_end)
);

CREATE TABLE IF NOT EXISTS foreign_flow_daily (
    symbol TEXT,
    trade_date TEXT,
    buy_value REAL,
    sell_value REAL,
    net_value REAL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS technical_indicators_daily (
    symbol TEXT,
    trade_date TEXT,
    ma20 REAL,
    ma50 REAL,
    ma200 REAL,
    rsi14 REAL,
    macd REAL,
    macd_signal REAL,
    atr14 REAL,
    volume_ma20 REAL,
    PRIMARY KEY (symbol, trade_date)
);

CREATE TABLE IF NOT EXISTS signals (
    symbol TEXT,
    trade_date TEXT,
    strategy_name TEXT,
    score REAL,
    metadata_json TEXT,
    PRIMARY KEY (symbol, trade_date, strategy_name)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    strategy_name TEXT,
    started_at TEXT,
    ended_at TEXT,
    params_json TEXT,
    metrics_json TEXT,
    benchmark_json TEXT
);

CREATE TABLE IF NOT EXISTS backtest_trades (
    run_id TEXT,
    symbol TEXT,
    entry_date TEXT,
    exit_date TEXT,
    entry_price REAL,
    exit_price REAL,
    shares REAL,
    pnl REAL,
    pnl_pct REAL,
    exit_reason TEXT,
    PRIMARY KEY (run_id, symbol, entry_date)
);
