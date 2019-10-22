# SPDX-License-Identifier: GPL-2.0-only
import logging
from logging import handlers

"""
Loggers and logging functions for setting up and using logging.
"""

default_logger = logging.getLogger()
default_logger.setLevel(logging.DEBUG)
default_fmt = "%(asctime)s - %(name)s - %(levelname)s: %(message)s"
default_datefmt = "%y-%m-%dT%H:%M:%S"
default_fmttr = logging.Formatter(default_fmt, default_datefmt)
service_fmttr = logging.Formatter("[%(asctime)s] %(message)s", default_datefmt)


def add_rotating_file(to_logger, filename, suffix, days, fmttr=default_fmttr,
                      level=logging.INFO):
    """
    Sets up a rotating file for the logger with the given format and log level.
    """
    file_handler = handlers.TimedRotatingFileHandler(
        filename,
        when="D",
        interval=days
    )
    file_handler.setFormatter(fmttr)
    file_handler.suffix = suffix
    file_handler.setLevel(level)
    to_logger.addHandler(file_handler)


def add_stream(to_logger, fmttr=default_fmttr, level=logging.DEBUG):
    """
    Sets up a stream to the logger with the given format and log level.
    """
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmttr)
    stream_handler.setLevel(level)
    to_logger.addHandler(stream_handler)


urllib = logging.getLogger("urllib3")
urllib.setLevel(logging.CRITICAL)  # Ignore logs

matplotlib = logging.getLogger("matplotlib")
matplotlib.setLevel(logging.CRITICAL)   # Ignore logs

service_logger = logging.getLogger("arbiter_service")
service_logger.setLevel(logging.INFO)

debug_logger = logging.getLogger("arbiter")
debug_logger.setLevel(logging.DEBUG)

startup_logger = logging.getLogger("arbiter_startup")
startup_logger.setLevel(logging.DEBUG)
add_stream(startup_logger)  # Always stream the startup logger
