from cfgparser import cfg, shared
import cinfo
import logging
import itertools
import multiprocessing
import actions
import logdb
import time

logger = logging.getLogger("arbiter." + __name__)

# The amount of time before a high usage warning to log out about user usage
high_usage_warning_context = 10 + cfg.high_usage_watcher.threshold_period

def is_high_usage(allusers_hist):
    """
    Watches for high usage and returns whether appropriate action needs to be
    taken if high usage has been detected.

    allusers_hist: iter
        A iterable of a dict with "mem" and "cpu" usage representing all users
        at a point.
    """
    cpu_count = multiprocessing.cpu_count()
    if cfg.high_usage_watcher.div_cpu_thresholds_by_threads_per_core:
        cpu_count /= cinfo.threads_per_core
    cpu_threshold = cfg.high_usage_watcher.cpu_usage_threshold * cpu_count
    mem_threshold = cfg.high_usage_watcher.mem_usage_threshold

    return all(
        allusers["cpu"] > cpu_threshold * 100
        or allusers["mem"] > mem_threshold * 100
        for allusers in allusers_hist
    )


def get_high_usage_users(users):
    """
    Returns a list of high usage users.

    users: {}
        A dictionary of User(), identified by their uid.
    """
    return sorted(
        users.values(),
        reverse=True,
        key=lambda u: u.cpu_usage + u.mem_usage
    )[:cfg.high_usage_watcher.user_count]


def send_high_usage_email(allusers, users):
    """
    Sends a email about high usage on the machine.

    allusers: dict
        A dict with "mem" and "cpu" usage representing all users at a point.
    users: {}
        A dictionary of users organized by their uid and containing a User obj.
        Usage is pulled from the users.
    """
    top_users = get_high_usage_users(users)
    cpu_usage, mem_usage = allusers["cpu"], allusers["mem"]

    logger.info("Sending an overall high usage email")
    actions.send_high_usage_email(top_users, cpu_usage, mem_usage,
                                  cfg.email.admin_emails, cfg.email.from_email)
    log_high_usage(allusers, users)


def log_high_usage(allusers, users):
    """
    Logs out information about what caused a high usage warning.
    """
    logger.debug("Usage that caused warning: %s", allusers)
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
