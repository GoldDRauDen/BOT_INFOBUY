"""
Stock Scanner - Quet va phan tich nguon du lieu chung khoan
Task 14: Scheduler wrapper - quyet dinh task nao chay/skip.
"""
import sys
from pathlib import Path

# Them src vao path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from utils.logger import setup_logger
from utils.source_loader import load_sources, get_enabled_sources, print_sources, SourceError
from utils.config_loader import load_settings
from scanner.connectivity_tester import run_connectivity_test
from scanner.discovery_scanner import run_discovery_scan


def main():
    """Khoi dong ung dung."""
    print("=" * 50)
    print("  STOCK SCANNER - Khoi dong...")
    print("=" * 50)
    
    # Khoi tao logger
    logger = setup_logger()
    logger.info("Bat dau khoi dong Stock Scanner")
    
    # Dam bao thu muc output ton tai
    base_dir = Path(__file__).parent
    output_dir = base_dir / "output"
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Thu muc output: {output_dir}")
    
    # Load settings
    try:
        settings = load_settings()
        app_name = settings.get("app", {}).get("name", "Stock Scanner")
        app_version = settings.get("app", {}).get("version", "1.0.0")
        logger.info(f"Ung dung: {app_name} v{app_version}")
    except Exception as e:
        logger.error(f"Loi load settings: {e}")
        print(f"[LOI] Khong the load settings: {e}")
        sys.exit(1)
    
    # Load sources
    try:
        sources = load_sources()
        logger.info(f"Da load {len(sources)} nguon du lieu")
        
        # In danh sach
        print_sources(sources)
        
        # Chi lay nhung source dang enable
        enabled = get_enabled_sources()
        logger.info(f"Nguon dang hoat dong: {len(enabled)}")
        
        if enabled:
            print(f"\n  Nguon dang hoat dong ({len(enabled)}):")
            for src in enabled:
                print(f"    - {src.name}: {src.base_url}")
        
    except SourceError as e:
        logger.error(f"Loi cau hinh: {e}")
        print(f"\n[LOI CAU HINH]\n{e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Loi khong xac dinh: {e}")
        print(f"\n[LOI] {e}")
        sys.exit(1)
    
    # === SCHEDULER (Task 14) ===
    from scheduler.scheduler import Scheduler
    from scheduler.state_store import StateStore

    state_store = StateStore(logger=logger, base_dir=base_dir)
    scheduler = Scheduler(logger=logger, state_store=state_store,
                          config=settings.get("scheduler", {}))
    decisions = scheduler.decide()

    print("\n" + "-" * 50)
    print("  Scheduler")
    print("-" * 50)
    for task, status in decisions.items():
        icon = {"run": "RUN ", "skip": "SKIP", "failed": "FAIL"}[status]
        print(f"    [{icon}] {task}")

    def _should_run(task):
        """Task co duoc chay khong (theo scheduler)."""
        return decisions.get(task) == "run"

    # === CONNECTIVITY TEST ===
    print("\n" + "-" * 50)
    print("  Connectivity Test")
    print("-" * 50)

    if _should_run("connectivity"):
        logger.info("Bat dau kiem tra ket noi...")

        try:
            results = run_connectivity_test(logger)
            
            # In ket qua
            print("\n  Chi tiet:")
            for r in results:
                status_icon = "[OK ]" if r.reachable else "[ERR]"
                print(f"    {status_icon} {r.name}: {r.status}")
                if r.reachable:
                    print(f"        Time: {r.response_time_ms}ms, SSL: {r.ssl_ok}")
                else:
                    print(f"        {r.error}")
            
            print(f"\n  Bao cao: output/connectivity_report.json")
            
        except Exception as e:
            logger.error(f"Loi khi kiem tra ket noi: {e}")
            print(f"\n[LOI] Kiem tra ket noi that bai: {e}")
            decisions["connectivity"] = "failed"
    else:
        print("\n  [SKIP] Connectivity (scheduler)")

    # === DISCOVERY SCAN ===
    print("\n" + "-" * 50)
    print("  Discovery Scan")
    print("-" * 50)

    if _should_run("discovery"):
        logger.info("Bat dau kham pha endpoint...")

        try:
            discovery_results = run_discovery_scan(logger)
            
            print(f"\n  Bao cao: output/discovery_report.json")
            
        except Exception as e:
            logger.error(f"Loi khi kham pha: {e}")
            print(f"\n[LOI] Kham pha that bai: {e}")
            decisions["discovery"] = "failed"
    else:
        print("\n  [SKIP] Discovery (scheduler)")

    # === DISCOVERY ENHANCEMENT (Task 15) ===
    print("\n" + "-" * 50)
    print("  Discovery Enhancement")
    print("-" * 50)

    if _should_run("enhancer"):
        logger.info("Tim endpoint that qua HTML/JS/source maps...")

        try:
            from enhancer.engine import run_discovery_enhancement
            enhanced_report = run_discovery_enhancement(logger)
            print(f"\n  Bao cao: output/enhanced_discovery_report.json")
        except Exception as e:
            logger.error(f"Loi khi discovery enhancement: {e}")
            print(f"\n[LOI] Discovery enhancement that bai: {e}")
            decisions["enhancer"] = "failed"
    else:
        print("\n  [SKIP] Discovery Enhancement (scheduler)")

    # === CAPABILITY TEST ===
    print("\n" + "-" * 50)
    print("  Capability Test")
    print("-" * 50)

    if _should_run("capability"):
        logger.info("Bat dau phan tich capability...")

        try:
            from scanner.capability_analyzer import run_capability_test
            capability_report = run_capability_test(logger)

            supported_count = sum(
                1 for source_name, caps in capability_report.items()
                if source_name != "generated_at"
                for cap in caps.values()
                if cap.get("status") == "supported"
            )
            print(f"\n  Tong capability duoc ho tro: {supported_count}")

        except Exception as e:
            logger.error(f"Loi khi phan tich capability: {e}")
            print(f"\n[LOI] Phan tich capability that bai: {e}")
            decisions["capability"] = "failed"
    else:
        print("\n  [SKIP] Capability (checksum discovery khong doi)")

    # === API REVERSE ENGINEERING (Task 16) ===
    print("\n" + "-" * 50)
    print("  API Reverse Engineering")
    print("-" * 50)

    if _should_run("reverser"):
        logger.info("Reverse engineer API endpoints...")

        try:
            from reverser.engine import run_reverse_engineering
            profiles_report = run_reverse_engineering(logger)
            print(f"\n  Bao cao: output/endpoint_profiles.json")
        except Exception as e:
            logger.error(f"Loi khi reverse engineering: {e}")
            print(f"\n[LOI] Reverse engineering that bai: {e}")
            decisions["reverser"] = "failed"
    else:
        print("\n  [SKIP] API Reverse Engineering (scheduler)")

    # === INDEX CRAWLER (Task 3) ===
    print("\n" + "-" * 50)
    print("  Index Crawler")
    print("-" * 50)

    if _should_run("crawler"):
        logger.info("Thu thap URL index...")

        try:
            from crawler.index_crawler import run_index_crawl
            index_report = run_index_crawl(logger)
            print(f"\n  Bao cao: output/index_pages.json")
        except Exception as e:
            logger.error(f"Loi khi crawl index: {e}")
            print(f"\n[LOI] Crawl index that bai: {e}")
            index_report = {}
            decisions["crawler"] = "failed"
    else:
        print("\n  [SKIP] Index Crawler (checksum discovery khong doi)")
        index_report = {}

    # === URL SELECTOR (Task 7) ===
    print("\n" + "-" * 50)
    print("  URL Selector")
    print("-" * 50)

    if _should_run("url_selector"):
        logger.info("Xay dung endpoint plan...")

        try:
            from builder.url_selector import run_url_selector
            plan_report = run_url_selector(logger)
            print(f"\n  Bao cao: output/endpoint_plan.json")
        except Exception as e:
            logger.error(f"Loi khi build endpoint plan: {e}")
            print(f"\n[LOI] Build endpoint plan that bai: {e}")
            plan_report = {}
            decisions["url_selector"] = "failed"
    else:
        print("\n  [SKIP] URL Selector (checksum capability khong doi)")
        plan_report = {}

    # === DATA FETCHER (Task 8) ===
    print("\n" + "-" * 50)
    print("  Data Fetcher")
    print("-" * 50)

    if _should_run("fetcher"):
        logger.info("Fetch du lieu theo plan...")

        try:
            from fetcher.data_fetcher import run_data_fetcher
            fetch_report = run_data_fetcher(logger)
            print(f"\n  Raw data: output/raw_data/")
        except Exception as e:
            logger.error(f"Loi khi fetch data: {e}")
            print(f"\n[LOI] Fetch data that bai: {e}")
            fetch_report = {}
            decisions["fetcher"] = "failed"
    else:
        print("\n  [SKIP] Data Fetcher (endpoint plan khong doi)")
        fetch_report = {}

    # === SCHEMA VALIDATOR (Task 9) ===
    print("\n" + "-" * 50)
    print("  Schema Validator")
    print("-" * 50)

    if _should_run("schema_validator"):
        logger.info("Validate schema raw data...")

        try:
            from validators.schema import run_schema_validator
            schema_report = run_schema_validator(logger)
            print(f"\n  Validated data: output/validated_data/")
        except Exception as e:
            logger.error(f"Loi khi validate schema: {e}")
            print(f"\n[LOI] Validate schema that bai: {e}")
            schema_report = {}
            decisions["schema_validator"] = "failed"
    else:
        print("\n  [SKIP] Schema Validator (khong co raw data moi)")
        schema_report = {}

    # === QUALITY GATE (Task 10) ===
    print("\n" + "-" * 50)
    print("  Quality Gate")
    print("-" * 50)

    if _should_run("quality_gate"):
        logger.info("Danh gia chat luong du lieu...")

        try:
            from validators.quality import run_quality_gate
            quality_report = run_quality_gate(logger)
            print(f"\n  Bao cao: output/quality_report.json")
        except Exception as e:
            logger.error(f"Loi khi danh gia chat luong: {e}")
            print(f"\n[LOI] Danh gia chat luong that bai: {e}")
            quality_report = {}
            decisions["quality_gate"] = "failed"
    else:
        print("\n  [SKIP] Quality Gate (khong co validated data moi)")
        quality_report = {}

    # === MASTER REPORT (Task 11) ===
    print("\n" + "-" * 50)
    print("  Master Report")
    print("-" * 50)

    if _should_run("master_report"):
        logger.info("Tao bao cao tong hop...")

        try:
            from reporters.master_report import run_master_report
            final_report = run_master_report(logger)
        except Exception as e:
            logger.error(f"Loi khi tao bao cao tong hop: {e}")
            print(f"\n[LOI] Tao bao cao tong hop that bai: {e}")
            decisions["master_report"] = "failed"
    else:
        print("\n  [SKIP] Master Report (scheduler)")
        final_report = {}

    # === DATA EXTRACTION (Task 13) ===
    print("\n" + "-" * 50)
    print("  Data Extraction")
    print("-" * 50)

    if _should_run("extraction"):
        logger.info("Trich xuat du lieu chung khoan...")

        try:
            from extractor.engine import run_extraction
            extraction_report = run_extraction(logger)
            print(f"\n  Extracted data: output/extracted_data/")
        except Exception as e:
            logger.error(f"Loi khi trich xuat du lieu: {e}")
            print(f"\n[LOI] Trich xuat du lieu that bai: {e}")
            decisions["extraction"] = "failed"
    else:
        print("\n  [SKIP] Data Extraction (khong co du lieu moi)")

    # === SAVE STATE + ARCHIVE (Task 14) ===
    scheduler.save_state(decisions)
    history_ts = state_store.archive_run()
    print(f"\n  State: state/pipeline_state.json")
    print(f"  History: history/{history_ts}/")

    # === KET THUC ===
    print("\n" + "=" * 50)
    print("  Khoi dong hoan tat!")
    print("  Log: output/logs/app.log")
    print("=" * 50)
    
    logger.info("Khoi dong hoan tat")


if __name__ == "__main__":
    main()
