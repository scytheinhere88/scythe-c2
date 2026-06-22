# app/core/logger.py
import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from app.core.config import settings

# Define log format
LOG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Warna untuk console (optional)
class CustomFormatter(logging.Formatter):
    """Formatter dengan warna untuk console."""
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    green = "\x1b[32;20m"
    cyan = "\x1b[36;20m"
    reset = "\x1b[0m"

    FORMATS = {
        logging.DEBUG: grey + LOG_FORMAT + reset,
        logging.INFO: green + LOG_FORMAT + reset,
        logging.WARNING: yellow + LOG_FORMAT + reset,
        logging.ERROR: red + LOG_FORMAT + reset,
        logging.CRITICAL: bold_red + LOG_FORMAT + reset,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno, LOG_FORMAT)
        formatter = logging.Formatter(log_fmt, DATE_FORMAT)
        return formatter.format(record)


def setup_logger(name: str = "scythe_c2") -> logging.Logger:
    """
    Setup logger dengan console handler (warna) dan file handler (rotating).
    Gunakan settings.LOG_LEVEL dan settings.LOG_FILE.
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    logger.propagate = False

    if logger.hasHandlers():
        logger.handlers.clear()

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(CustomFormatter())
    logger.addHandler(console_handler)

    # File Handler (Rotating)
    if settings.LOG_FILE:
        log_path = Path(settings.LOG_FILE)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_path,
                maxBytes=10_485_760,
                backupCount=5,
                encoding='utf-8'
            )
            file_handler.setLevel(logging.DEBUG)
            file_formatter = logging.Formatter(LOG_FORMAT, DATE_FORMAT)
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"Failed to create log file: {e}", file=sys.stderr)

    return logger


# Default logger instance
logger = setup_logger()

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"scythe_c2.{name}")

def init_logging():
    logger.info("Logging system initialized.")

# ========== FUNGSI YANG DITAMBAHKAN ==========
def log_attack_event(attack_id: str, event: str, details: dict = None):
    msg = f"[ATTACK] {attack_id} | {event}"
    if details:
        msg += f" | {details}"
    logger.info(msg)

def log_bot_event(bot_id: str, event: str, details: dict = None):
    msg = f"[BOT] {bot_id} | {event}"
    if details:
        msg += f" | {details}"
    logger.info(msg)