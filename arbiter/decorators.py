# SPDX-License-Identifier: GPL-2.0-only
"""
A module for common decorators.
"""
import time
from functools import wraps


def retry(exceptions,
          logger,
          tries=3,
          delay=0.2,
          backoff=2):
    """
    Retry calling the decorated function using an exponential backoff.

    exceptions: tuple()
        The exceptions to check.
    logger: logging
        Logger to output logging information to.
    tries: int
        Number of times to try before giving up.
    delay: float
        Initial delay between retries in seconds.
    backoff: float
        Backoff multiplier (e.g. value of 2 will double the delay each retry).
    """
    def deco_retry(func):
        @wraps(func)
        def f_retry(*args, **kwargs):
            mtries, mdelay = tries, delay
            trace = ""
            while mtries > 1:
                try:
                    return func(*args, **kwargs)
                except exceptions as stacktrace:
                    trace = stacktrace
                    msg = "{}, Retrying in {} seconds...".format(stacktrace,
                                                                 mdelay)
                    logger.warning(msg)
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay *= backoff

            logger.error(trace)
            return func(*args, **kwargs)
        return f_retry
    return deco_retry
