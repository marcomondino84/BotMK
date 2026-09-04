"""
Sistema de logging estructurado para el seguimiento de eventos y operaciones financieras.
"""

import logging
import sys
from pathlib import Path


def setup_logger(name: str = "BotMK") -> logging.Logger:
    """
    Configura y retorna un logger estandarizado con salida a consola y archivo.
    """
    logger = logging.getLogger(name)
    
    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s:%(lineno)d] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Handler para consola
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Handler para archivo de log
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    file_handler = logging.FileHandler(logs_dir / "bot_activity.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()
