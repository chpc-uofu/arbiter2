"""
Makes decisions on what actions should be made based on a user.User() (Actions
are "triggered"). These triggers, and the action calls are defined in
evaluate(). This method is called every arbiter interval for each active user.

For calculations of badness, calc_badness() calculates a user.User()'s delta
badness score.
"""

import logging
import collections
import time
import actions
import statuses
import logdb
from cfgparser import cfg, shared
import integrations

logger = logging.getLogger("arbiter." + __name__)
service_logger = logging.getLogger("arbiter_service")


def evaluate(user_obj):
    """
    When run, checks the specified triggers and takes the specified action
    associated.

    user_obj: user.User()
        A user to evaluate.
    """
    uid = user_obj.uid
    status = user_obj.status
    username = "{} ({})".format(*integrations._get_name(uid))
    badness = user_obj.badness_history[0]["badness"]
    badness_score = sum(badness.values())

    # Get when released from penalty
    timeout = -1
    if statuses.lookup_is_penalty(status.current):
        timeout = statuses.lookup_status_prop(status.current).timeout

    if status.current != status.default:
        logger.debug("%s has status: %s", uid, status)

    # Only evaluate users who are not in penalty
    if not statuses.lookup_is_penalty(status.current):
        if badness_score >= 100:
            logger.info("Increasing the penalty status of %s", user_obj.uid)
            new_status = _upgrade_penalty(user_obj)
            service_logger.info("User %s was put in: %s", username, new_status)

        elif _eval_lower_occurrences(badness, status):
            logger.info("Lowering the occurrences count of %s", uid)
            service_logger.info("User %s penalty occurrences has lowered to: "
                                "%s", username, status.occurrences - 1)
            statuses.update_occurrences(uid, -1, update_timestamp=True)

        # If the user is being bad
        elif badness_score > 0:
            logger.debug("%s has nonzero badness: %s", uid, badness)
            service_logger.info("User %s has nonzero badness: %s", username,
                                badness_score)
            whlist_cpu_usage, whlist_mem_usage = user_obj.avg_proc_usage(whitelisted=True)
            # Print out whitelisted usage vs normal usage for debugging
            logger.debug("Whitelisted Usage: cpu %s, mem %s",
                         whlist_cpu_usage, whlist_mem_usage)
            logger.debug("Real Usage: %s", {
                "cpu": user_obj.cpu_usage,
                "mem": user_obj.mem_usage
            })

    # Lower status for bad users past a certain time
    # TODO (Dylan): Make this applicable to more than just penalty groups
    elif time.time() - int(status.timestamp) >= timeout:
        logger.info("Decreasing the penalty status of %s", user_obj.uid)
        new_status = _lower_penalty(user_obj)
        service_logger.info("User %s is now in: %s", username, new_status)

    # If their in penalty, but haven't been released
    elif timeout != -1:
        timeleft = int(time.time()) - status.timestamp
        logger.debug("%s has spent: %s seconds in penalty of a required %s",
                     uid, timeleft, timeout)


def _eval_lower_occurrences(latest_badness, status):
    """
    Evaluates whether a user's penalty needs to be lowered.
    """
    expected_lower_time = time.time() - cfg.status.penalty.occur_timeout
    occurrences_timed_out = status.occur_timestamp < expected_lower_time
    been_bad = sum(latest_badness.values()) != 0
    return status.occurrences > 0 and occurrences_timed_out and not been_bad


def _lower_penalty(user_obj):
    """
    Lowers the penalty status of a user.

    user_obj: user.User()
        A user to lower the penalty of.
    """
    default_status = user_obj.status.default
    actions.update_status(user_obj.cgroup, default_status, default_status)
    # Update timestamp of occurrences
    statuses.update_occurrences(user_obj.uid, 0, update_timestamp=True)
    actions.user_nice_email(user_obj, default_status)
    return default_status


def _upgrade_penalty(user_obj):
    """
    Upgrades the penalty status of a user.

    user_obj: user.User()
        A user to upgrade the penalty of.
    """
    new_status = actions.upgrade_penalty(user_obj.cgroup, user_obj.status)

    # Add the record of the action to the database
    rotated_filename = logdb.rotated_filename(
        cfg.database.log_location + "/" + shared.logdb_name,
        cfg.database.log_rotate_period,
        shared.log_datefmt
    )
    logdb.add_action(new_status, user_obj.uid, user_obj.history,
                     int(time.time()), rotated_filename)

    actions.user_warning_email(user_obj, new_status)
    return new_status


def calc_badness(user_obj):
    """
    Computes a delta badness score. Returns a dict with "cpu" and "mem" delta
    badness.

    user_obj: user.User()
        A user to calculate the badness of.

    >>> _default_calc_badness()
    {"cpu": 0.0, "mem": 52.356116402}
    """
    refresh = cfg.general.arbiter_refresh
    time_to_max_bad = cfg.badness.time_to_max_bad
    time_to_min_bad = cfg.badness.time_to_min_bad
    mem_quota = user_obj.mem_quota
    cpu_quota = user_obj.cpu_quota

    whlist_cpu_usage, whlist_mem_usage = user_obj.avg_proc_usage(whitelisted=True)
    # Only subtract whlist_cpu_usage, since too much memory usage is still
    # bad, regardless of whether it's whitelisted (it cannot be throttled once
    # allocated).
    bad_mem = user_obj.mem_usage
    bad_cpu = user_obj.cpu_usage - whlist_cpu_usage

    Metric = collections.namedtuple("Metric", "quota usage threshold")
    metrics = {
        "mem": Metric(mem_quota, bad_mem, cfg.badness.mem_badness_threshold),
        "cpu": Metric(cpu_quota, bad_cpu, cfg.badness.cpu_badness_threshold)
    }
    new_delta_badness = {}
    for name, metric in metrics.items():
        # Calculate the increase/decrease in badness (to translate the time
        # and extreme scores to a change per interval)
        max_incr_per_sec = 100.0 / (time_to_max_bad * metric.threshold)
        max_incr_per_interval = max_incr_per_sec * refresh
        max_decr_per_sec = 100.0 / time_to_min_bad
        max_decr_per_interval = max_decr_per_sec * refresh

        usage = metric.usage
        # Make badness scores consistent between debug and non-debug mode or
        # Optionally cap the badness increase by capping the usage
        if cfg.general.debug_mode or cfg.badness.cap_badness_incr:
            usage = min(metric.usage, metric.quota)

        rel_usage = usage / metric.quota
        if rel_usage >= metric.threshold:
            change = rel_usage * max_incr_per_interval
        else:
            change = (1 - rel_usage) * -max_decr_per_interval
        new_delta_badness[name] = change
    return new_delta_badness

