# BOT_INFOBUY — Stock Scanner + Phân tích định lượng

Quét và phân tích dữ liệu chứng khoán (HOSE, HNX, UPCOM). Pipeline baseline **16 task** (deterministic, Evidence First) + tầng phân tích định lượng **Phase 1-4+6** (data layer, indicators, screening, backtest, risk).

> **Phase 5 (ML) tạm hoãn** — không train model ML trong phase này. Rule-based + walk-forward backtest.

## Yêu cầu

- Python **>= 3.10** (GHA dùng 3.11)
- Cài đặt: `pip install -r requirements.txt`

## Cấu trúc

```
BOT_INFOBUY/
├── config/
│   ├── sources.yaml          # Danh sách nguồn dữ liệu (HOSE/HNX/UPCOM)
│   ├── settings.yaml         # timeout, retry, scheduler, ai.watchlist
│   └── screening_rules.yaml  # Rules screening (ma_breakout_volume_confirm, value_screening)
├── data/
│   ├── schema.sql            # Schema SQLite (8 bảng)
│   └── bot_buy.db            # DB chính (sinh ra khi chạy fetchers)
├── src/
│   ├── db/database.py        # connect, init_db, upsert generic (không ORM)
│   ├── fetcher/              # Task 8 + symbol/ohlcv/fundamental/foreign_flow fetchers
│   ├── indicators/           # technical_indicators, fundamental_screening
│   ├── screening/rule_engine.py
│   ├── backtest/             # engine (event-driven), walk_forward
│   ├── risk/correlation_check.py
│   ├── scanner/ crawler/ builder/ validators/ reporters/ extractor/
│   ├── enhancer/ reverser/ scheduler/ utils/
│   ├── analyst/ reviewer/
├── tests/                    # 393 baseline + tests mới (deterministic, mock HTTP)
├── output/                   # Báo cáo pipeline
├── main.py                   # Pipeline 16 task
└── send_telegram.py          # Báo cáo Telegram (giữ hàm cũ + báo cáo định lượng)
```

## Phân tích định lượng (Phase 1-4+6)

### 1. Data layer

`data/schema.sql` — SQLite, 8 bảng: `symbols`, `ohlcv_daily`, `fundamentals_quarterly`, `foreign_flow_daily`, `technical_indicators_daily`, `signals`, `backtest_runs`, `backtest_trades`.

- Giá lưu **VND** (KBS trả nghìn VND → ×1000), volume đơn vị cổ phiếu, ngày `YYYY-MM-DD` (giờ VN UTC+7).
- Upsert theo PRIMARY KEY — chạy lại không trùng lặp.

### 2. Chạy các module

Chạy từ thư mục gốc theo thứ tự:

```bash
python -m src.fetcher.symbol_fetcher
python -m src.fetcher.historical_ohlcv_fetcher   # toàn bộ HOSE, ~10-15 phút (throttle 0.5s)
python -m src.fetcher.fundamental_fetcher
python -m src.fetcher.foreign_flow_fetcher       # optional — không có nguồn ổn định → skip, không fail
python -m src.indicators.technical_indicators
python -m src.screening.rule_engine              # output top 20 ma theo score
python -m src.backtest.walk_forward              # windows: train 2 năm / test 6 tháng / bước 6 tháng
python -m src.risk.correlation_check             # tương quan 60 phiên top N + cảnh báo tập trung ngành
```

### 3. Chạy test

```bash
pytest -q    # 393 test baseline + tests mới, toàn bộ phải PASS
```

### 4. Telegram report

`send_telegram.py` giữ nguyên luồng cũ (fetch vietstock + Gemini) và bổ sung báo cáo định lượng từ DB:
top 10 mã theo score, walk-forward metrics (nếu chưa có → ghi *"chua co backtest"*), so sánh VNINDEX, cảnh báo tương quan.

Credentials: env var `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY` (không hardcode secret). Thiếu credential → in `SKIP`, exit 0 (không fail CI).

### 5. CI (GitHub Actions)

`.github/workflows/scanner.yml` — 3 jobs:

| Job | Nội dung |
|-----|----------|
| `fetch-data` | symbol → OHLCV → fundamental → foreign flow → indicators → commit `data/bot_buy.db` |
| `screening-and-report` | rule engine → correlation check → Telegram report (env secrets) |
| `weekly-backtest` | walk-forward → commit DB — chỉ chạy khi schedule `0 15 * * 1` |

Cron chạy `0 15 * * 1-5` (22:00 VN) + `workflow_dispatch`.

## Nguồn dữ liệu (Phase 1-4+6)

- **OHLCV + benchmark**: `vnstock==4.0.6`, source **KBS** (KB Securities), fallback MSN (~1 năm). VNINDEX làm benchmark.
- **Danh sách mã + ngành**: `Listing.symbols_by_exchange()` (filter `type=='stock'`) + `symbols_by_industries()`.
- **Fundamental**: `Finance.ratio(...)` + `income_statement(...)` quarterly. Community edition chỉ 8 kỳ (2 năm). Không có `reported_date` từ nguồn → ước lượng = cuối quý + 20 ngày, `is_estimated=1`.
- **Foreign flow**: không có nguồn free ổn định → module log `"foreign flow: khong co nguon on dinh, skip"`, trả 0, **không fail pipeline**. Không bịa số liệu — nguồn chết thì bỏ qua và ghi rõ trong log.

## Giới hạn (ghi chép spec §14)

- Fundamental: 8 kỳ (community), reported_date ước lượng.
- Foreign flow: optional, thường không có dữ liệu.
- Phase 5 (ML): tạm hoãn, chưa thêm lightgbm.
- RSI/MACD/ATR: công thức tự viết, không dùng ta-lib.

## Log

Log ghi vào: `output/logs/app.log`

## T?ng t? v?n an to?n (Phase 7)

Ch?y trong job `screening-and-report` h?ng ng?y:
- `src.risk.market_regime` ? tr?ng th?i th? tr??ng (VNINDEX vs MA200 ? BULLISH/BEARISH).
- `src.screening.rule_engine` ? ch?m ?i?m to?n m?, ?u ti?n rule `safe_quality_growth`
  (trend d?i h?n + thanh kho?n + ??nh gi? h?p l?).
- `src.portfolio.manager` ? danh m?c m?u 10 m?: 10% v?n/m? (t?i ?a 15%), c?t l? 8%,
  t?i c?n b?ng m?i th? 2. Ghi `portfolio_positions` + `portfolio_trades` v?o DB.
- `src.risk.correlation_check` ? c?nh b?o r?i ro t?p trung ng?nh.
- B?o c?o Telegram: th? tr??ng ? top 10 an to?n + l? do ? danh m?c m?u PnL ? c?nh b?o.
- Backfill l?ch s? s?u: `python -m src.fetcher.historical_ohlcv_fetcher --backfill`
  (1 l?n, fetch t? 2015; KBS tr? t?i ?a ~1992 phi?n/m?, t?c t? ~2018).
- Ngu?n d? li?u: vnstock 4.0.6 (KBS). ??ng k? key mi?n ph? t?i vnstocks.com ?? c?
  60 request/ph?t thay v? 20 (guest); ??t v?o GitHub Secrets `VNSTOCK_API_KEY`.

## Ki?m ch?ng hi?u qu?

Quy t?c b?t bi?n: signal ch? hi?n th? trong b?o c?o khi ?? qua backtest/paper theo d?i.
S? li?u (CAGR/Sharpe/MaxDD/WinRate) ???c ghi r? trong b?o c?o, k? c? khi thua benchmark.
