# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
A module for common decorators.
"""

import time
import functools


def retry(exceptions, logger, tries=3, delay=0.2, backoff=2):
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
    def deco_retry(deco_func):
        @functools.wraps(deco_func)
        def f_retry(*args, **kwargs):
            mtries, mdelay = tries, delay
            trace = ""
            while mtries > 1:
                try:
                    return deco_func(*args, **kwargs)
                except exceptions as stacktrace:
                    trace = stacktrace
                    msg = "{}, Retrying in {} seconds...".format(stacktrace,
                                                                 mdelay)
                    logger.warning(msg)
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay *= backoff

            logger.error(trace)
            return deco_func(*args, **kwargs)
        return f_retry
    return deco_retry


def func_on_exception(exceptions, logger, desc, func):
    """
    When a given exception occurs the error is logged and the given function
    is called and the result returned.

    exceptions: tuple()
        The exceptions to check.
    logger: logging
        Logger to output logging information to.
    desc: str
        A prefix to add to logging information.
    func: lambda
        A function that takes in the same arguments as the wrapped function.
    """
    def deco_func_on_exception(deco_func):
        @functools.wraps(deco_func)
        def f_func_on_exception(*args, **kwargs):
            try:
                return deco_func(*args,**kwargs)
            except exceptions as err:
                logger.warning(desc + " " + str(err))
                return func(*args, **kwargs)
        return f_func_on_exception
    return deco_func_on_exception
