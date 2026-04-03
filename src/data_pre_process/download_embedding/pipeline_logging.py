import logging
import os


def setup_logging(log_dir="logs"):
    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(os.path.join(log_dir, "stage4_embed"), exist_ok=True)

    fmt     = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Guard: do not attach duplicate handlers on re-import / re-call
    if root.handlers:
        return

    fh = logging.FileHandler(
        os.path.join(log_dir, "pipeline.log"), encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(fmt, datefmt))

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(fmt, datefmt))

    root.addHandler(fh)
    root.addHandler(ch)


def get_stage_logger(name, log_path):
    """为每个阶段创建独立 logger，同时写入阶段专属文件和 pipeline.log。"""
    logger = logging.getLogger(name)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        "%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(fh)
    return logger
