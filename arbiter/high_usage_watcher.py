from cfgparser import cfg, shared
import cinfo
import logging
import itertools
import multiprocessing
import actions
import logdb
import time

import usage

logger = logging.getLogger("arbiter." + __name__)

# The amount of time before a high usage warning to log out about user usage
high_usage_warning_context = 10 + cfg.high_usage_watcher.threshold_period

def is_high_usage(usage_hist):
    """
    Watches for high usage and returns whether appropriate action needs to be
    taken if high usage has been detected.

    usage_hist: iter
        A iterable of a usage dicts representing the usage of all the users.
    """
    cpu_count = multiprocessing.cpu_count()
    if cfg.high_usage_watcher.div_cpu_thresholds_by_threads_per_core:
        cpu_count /= cinfo.threads_per_core
    cpu_threshold = cfg.high_usage_watcher.cpu_usage_threshold * cpu_count
    mem_threshold = cfg.high_usage_watcher.mem_usage_threshold

    return all(
        usage["cpu"] > cpu_threshold * 100
        or usage["mem"] > mem_threshold * 100
        for usage in usage_hist
    )


def get_high_usage_users(users):
    """
    Returns a list of high usage users.

    users: {}
        A dictionary of User(), identified by their uid.
    """
    # We're going to judge everyone by their usage relative to the entirety
    # of the machine, instead of status quotas, since we only care about usage,
    # not a persons status.
    return usage.rel_sorted(
        users.values(),
        multiprocessing.cpu_count() * 100, 100,
        key=lambda u: (u.cpu_usage, u.mem_usage),
        reverse=True
    )[:cfg.high_usage_watcher.user_count]


def send_high_usage_email(usage, users):
    """
    Sends a email about high usage on the machine.

    usage: dict
        A usage dict containing metrics and usage.
    users: {}
        A dictionary of User(), identified by their uid.
    """
    top_users = get_high_usage_users(users)
    logger.info("Sending an overall high usage email")
    actions.send_high_usage_email(top_users, usage["cpu"], usage["mem"])
    log_high_usage(usage, users)


def log_high_usage(usage, users):
    """
    Logs out information about what caused a high usage warning.

    usage: dict
        A usage dict containing metrics and usage.
    users: dict
        A dictionary of User(), identified by their uid.
    """
    logger.debug("Usage that caused warning: %s", usage)
    rotated_filename = logdb.rotated_filename(
        cfg.database.log_location + "/" + shared.logdb_name,
        cfg.database.log_rotate_period,
        shared.log_datefmt
    )
    timestamp = int(time.time())
    for user_obj in users.values():
        history_len = min(len(user_obj.history), high_usage_warning_context)
        user_history = itertools.islice(user_obj.history, history_len)
        logdb.add_action("high_usage_warning", user_obj.uid, user_history,
                         timestamp, rotated_filename)
