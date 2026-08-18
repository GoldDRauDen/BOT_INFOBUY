import logging
import os
from pathlib import Path


def setup_logger(name: str = "stock_scanner", log_file: str = "app.log") -> logging.Logger:
    """Khởi tạo logger với file output."""
    output_dir = Path(__file__).parent.parent.parent / "output" / "logs"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    log_path = output_dir / log_file
    
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    
    if logger.handlers:
        return logger
    
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    return logger
