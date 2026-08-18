# BOT_INFOBUY - SPEC TRIEN KHAI (Phase 1-4 + 6)

## 0. BOI CANH
Project moi `BOT_INFOBUY` (F:\github\BOT_INFOBUY) = baseline copy tu bot_buy/stock-scanner
(16 task pipeline + 393 test, dang xanh). Spec nay THEM tang phan tich dinh luong theo
BOT_BUY_IMPROVEMENT_SPEC.md. Phase 5 (ML) TAM HOAN - ghi ro trong README.

## 1. NGUON DU LIEU (DA RECON + VERIFY THUC TE, KHONG BIA)
Dung thu vien `vnstock==4.0.6` (da cai san, pin version), source KBS (KB Securities).
- OHLCV lich su: `from vnstock.api.quote import Quote; Quote(symbol="ACB", source="KBS").history(start="2023-01-01", end="2026-08-18", interval="1D")` -> 902 dong 2023->2026. 
  Cot: time (datetime co gio 07:00 = UTC, phai normalize ve ngay), open/high/low/close (DON VI NGHIN VND, vi du 22.65 = 22,650 VND), volume (co phieu).
- VNINDEX benchmark: Quote(symbol="VNINDEX", source="KBS").history(...) -> 902 dong.
- Danh sach ma + san: `from vnstock.api.listing import Listing; Listing(source="KBS").symbols_by_exchange()` 
  -> cot: symbol, organ_name, en_organ_name, exchange (HOSE/HNX/UPCOM), type ('stock'/'bond'/...), id.
  Chi lay type=='stock'. HOSE ~400-500 ma.
- Nganh ICB: `Listing(source="KBS").symbols_by_industries()` -> cot symbol, industry_code, industry_name. 
  ~696 ma co nganh; ma khong co -> sector rong, khong bia.
- Fundamental: `from vnstock.api.financial import Finance; Finance(symbol="ACB", source="KBS").ratio(period="quarterly", lang="vi")`
  -> 32 chi so: trailing_eps, book_value_per_share_bvps, pe_ratio, pb_ratio, roe, roa, dividend_yield, beta, growth...
  + `Finance(...).income_statement(period="quarterly", lang="vi")` -> doanh thu, loi nhuan.
  GIOI HAN: community edition chi 8 ky (2 nam). KHONG CO reported_date tu nguon -> uoc luong:
  cuoi quy + 20 ngay, is_estimated=1 (dung quy tac spec).
- Foreign flow: KHONG co nguon free on dinh -> module fetch co the khong co du lieu.
  Ghi schema day du, fetch thu, neu loi/skip thi GHI RO trong log + quality gate KHONG fail pipeline.
- Fallback: neu KBS loi, thu MSN (`Quote(..., source="MSN")` chi ~1 nam lich su). Ghi ro gioi han.

## 2. RANG BUOC BAT BIEN
- Giu NGUYEN baseline 16 task + 393 test hien co, khong xoa/sua file cu tru khi spec yeu cau.
- Python 3.10+ (GHA dung 3.11). Code comment/docstring TIENG VIET.
- Secret chi qua env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GEMINI_API_KEY) - KHONG hardcode.
- Khong bia so lieu: nguon chet thi bo qua + log ro, khong tao so gia.
- Upsert theo PRIMARY KEY, chay lai khong trung lap.
- Don vi ghi ro: gia NGHIN VND, volume co phieu, thoi gian theo gio VN UTC+7.

## 3. DATA LAYER
File `data/schema.sql` (SQLite, sqlite3 built-in). Bang:
- symbols(symbol TEXT PK, organ_name, en_organ_name, exchange, sector, industry_code, is_active INT DEFAULT 1, updated_at)
- ohlcv_daily(symbol TEXT, trade_date TEXT, open REAL, high REAL, low REAL, close REAL, volume INT, source TEXT, PRIMARY KEY(symbol, trade_date))
- fundamentals_quarterly(symbol TEXT, quarter_end TEXT, reported_date TEXT, is_estimated INT, eps REAL, pe_ratio REAL, pb_ratio REAL, roe REAL, roa REAL, bvps REAL, revenue REAL, net_income REAL, dividend_yield REAL, PRIMARY KEY(symbol, quarter_end))
- foreign_flow_daily(symbol TEXT, trade_date TEXT, buy_value REAL, sell_value REAL, net_value REAL, PRIMARY KEY(symbol, trade_date))
- technical_indicators_daily(symbol TEXT, trade_date TEXT, ma20 REAL, ma50 REAL, ma200 REAL, rsi14 REAL, macd REAL, macd_signal REAL, atr14 REAL, volume_ma20 REAL, PRIMARY KEY(symbol, trade_date))
- signals(symbol TEXT, trade_date TEXT, strategy_name TEXT, score REAL, metadata_json TEXT, PRIMARY KEY(symbol, trade_date, strategy_name))
- backtest_runs(run_id TEXT PK, strategy_name, started_at, ended_at, params_json, metrics_json, benchmark_json)
- backtest_trades(run_id TEXT, symbol TEXT, entry_date TEXT, exit_date TEXT, entry_price REAL, exit_price REAL, shares REAL, pnl REAL, pnl_pct REAL, exit_reason TEXT, PRIMARY KEY(run_id, symbol, entry_date))

File `src/db/database.py`: connect(data/bot_buy.db), init_db(schema.sql), upsert helper generic
(bang, rows dict list, conflict key), get_conn. Khong ORM.

## 4. FETCHERS (src/fetcher/)
### symbol_fetcher.py (moi)
- `python -m src.fetcher.symbol_fetcher`: Listing.symbols_by_exchange() (filter type=='stock')
  + symbols_by_industries() join sector -> upsert symbols. Throttle + retry (settings.yaml fetcher).
### historical_ohlcv_fetcher.py (moi)
- Doc danh sach symbol HOSE (exchange=='HOSE') tu DB.
- Incremental: moi symbol chi fetch tu MAX(trade_date)+1 den hom nay; neu chua co -> tu 2023-01-01.
- Normalize time -> trade_date (YYYY-MM-DD, gio VN). Gia x1000 -> VND dong? QUYET DINH: LUU DONG VND (nhan 1000) de nhat quan voi send_telegram va report. Ghi ro trong code.
- Upsert ohlcv_daily. Throttle 0.5s/ma + retry 2. Log skip/loi tung ma, KHONG dung pipeline.
- Them VNINDEX vao ohlcv_daily (symbol='VNINDEX') tu KBS.
### fundamental_fetcher.py (moi)
- Doc symbols tu DB. Moi ma: Finance.ratio + income_statement quarterly.
- quarter_end = ten cot ky (vi du '2026-Q2') -> ngay cuoi quy. reported_date = quarter_end + 20 ngay, is_estimated=1.
- net_income lay tu income_statement (item co item_id lien quan loi nhuan sau thue).
- Upsert fundamentals_quarterly. Log ma loi.
### foreign_flow_fetcher.py (moi)
- Optional. Thu nguon (ghi ro URL thu). Neu khong co du lieu -> log "foreign flow: khong co nguon on dinh, skip" + return 0. KHONG fail.

## 5. INDICATORS (src/indicators/)
### technical_indicators.py (moi)
- `python -m src.indicators.technical_indicators`: doc ohlcv_daily, tinh theo tung symbol (pandas, vectorized):
  MA20/MA50/MA200 (close), RSI14 (Wilder), MACD (12/26/9 EMA), ATR14 (Wilder), volume_MA20.
  Cong thuc tu viet (khong dung ta-lib). Gia dong VND nhat quan.
- Upsert technical_indicators_daily. Chay sau fetch OHLCV.
### fundamental_screening.py (moi)
- Tinh PE/PB tuong doi so voi trung binh nganh (sector tu symbols; chi tinh neu sector co >=3 ma, nguoc lai None).
- Tang truong loi nhuan YoY tu fundamentals_quarterly (cung quy nam truoc). Ghi vao bien phu de rule engine dung.
- Khong co du lieu -> None, khong bia.

## 6. SCREENING (src/screening/rule_engine.py, moi)
- Config: `config/screening_rules.yaml` theo mau BOT_BUY_IMPROVEMENT_SPEC (ma_breakout_volume_confirm + value_screening).
- Engine: doc YAML, quet tat ca symbol co du lieu moi nhat, evaluate dieu kien (pandas query an toan),
  tinh score = tong weight cac rule khop. Ghi vao signals voi strategy_name = ten rule, metadata_json = danh sach dieu kien khop.
- Output console: top 20 ma theo score.
- YAML moi rule: name, conditions (list string pandas-expression), weight. Validator: condition phai parse duoc.

## 7. BACKTEST (src/backtest/)
### engine.py (moi)
- Event-driven tren ohlcv_daily: vao lenh khi signal xuat hien, thoat khi stop-loss/take-profit/het window.
- Phi giao dich 0.3%/chieu + slippage 0.1%. Position sizing % von (BacktestConfig.position_size_pct default 0.2).
- Metrics: CAGR, Sharpe (rf=0), Max Drawdown, Win rate. Benchmark: VNINDEX cung ky.
- Luu backtest_runs + backtest_trades. Ham `run_backtest(symbols, signals_df, params) -> metrics`.
### walk_forward.py (moi)
- `python -m src.backtest.walk_forward`: windows truot (train 2 nam, test 6 thang, buoc 6 thang), toi thieu 3 windows tren du lieu OHLCV 2023->2026.
- Output: metric trung binh + std giua windows. Neu std lon (CAGR std > mean) -> canh bao ro.
- Ghi run vao DB (strategy_name='wf_rule_based'). Khong train ML (Phase 5 defer).

## 8. RISK (src/risk/correlation_check.py, moi)
- Tinh ma tran tuong quan daily return (last 60 phien) cua top N ma de xuat.
- Neu >50% top N cung 1 nganh -> canh bao "rui ro tap trung nganh". In + luu vao metadata cho report.

## 9. TELEGRAM REPORT (send_telegram.py - CAP NHAT, giu ham cu)
- Doc signals moi nhat + backtest metrics moi nhat tu DB.
- Report: top 10 ma (score, ly do rule, gia, % thay doi), metric backtest cua moi strategy da qua WF,
  so sanh VNINDEX, canh bao tuong quan, Gemini tom tat (giu logic cu, model gemini-flash-latest).
- Neu khong co metric backtest -> ghi "chua co backtest" chu khong hien signal tran.
- Giu nguyen: doc token tu env, retry, cat neu vuot Telegram limit.

## 10. WORKFLOW GHA (.github/workflows/scanner.yml - THAY THE toan bo)
Theo spec goc: 3 job `fetch-data` -> `screening-and-report` -> `weekly-backtest` (if schedule '0 15 * * 1').
- Cron '0 15 * * 1-5' (22:00 VN) + workflow_dispatch. permissions: contents: write. concurrency group giu lai.
- python-version '3.11'. requirements.txt ca vnstock==4.0.6.
- fetch-data: symbol_fetcher -> historical_ohlcv_fetcher -> fundamental_fetcher -> foreign_flow_fetcher -> technical_indicators -> commit data/bot_buy.db.
- screening-and-report: checkout ref main -> rule_engine -> correlation_check -> send_telegram.py (env secrets).
- weekly-backtest: walk_forward -> commit DB.

## 11. requirements.txt (THEM, giu cu)
pandas>=2.0
numpy>=1.24
PyYAML>=6.0
vnstock==4.0.6
(khong them lightgbm - Phase 5 defer; khong them ta - tu viet cong thuc)

## 12. TESTS MOI (tests/, pytest, deterministic, mock HTTP - khong goi mang that)
- test_historical_ohlcv_fetcher.py: mock vnstock Quote.history tra DataFrame gio, test normalize + upsert khong trung.
- test_technical_indicators.py: du lieu gia nhan tao, doi chieu RSI/MACD/MA voi cong thuc tay (sai so < 0.01%).
- test_rule_engine.py: YAML parse + score dung.
- test_backtest_engine.py: du lieu gia da biet, kiem entry/exit/stop-loss/take-profit, phi + slippage.
- test_look_ahead_bias.py: join fundamental theo reported_date, dam bao khong co du lieu reported_date > current_date lo vao mau ngay do.
- TAT CA 393 test cu PHAI PASS (pytest -q toan bo).

## 13. VERIFY + COMMIT
Lenh chay (trong thu muc F:\github\BOT_INFOBUY):
1. python -m src.fetcher.symbol_fetcher
2. python -m src.fetcher.historical_ohlcv_fetcher   (toan bo HOSE, ~10-15 phut, throttle 0.5s)
3. python -m src.fetcher.fundamental_fetcher
4. python -m src.fetcher.foreign_flow_fetcher
5. python -m src.indicators.technical_indicators
6. python -m src.screening.rule_engine
7. python -m src.backtest.walk_forward
8. python -m src.risk.correlation_check
9. pytest -q  (393 cu + moi, toan bo PASS)
10. Kiem tra DB: SELECT COUNT(*) ohlcv_daily > 0 (phai >= 100000 cho HOSE 3 nam), khong trung PK.
Commit: "feat: BOT_INFOBUY phase 1-4+6 - data layer, indicators, screening, backtest, risk" + git push origin main (neu remote da co).
Khong commit secret. Khong commit output/ thay doi lon tu baseline (gitignore giu).

## 14. GHI CHEP
- Update README.md: mo ta kien truc moi, cach chay, gioi han (fundamental 8 ky, foreign flow optional, Phase 5 defer).
