from cfgparser import cfg
import cinfo
import multiprocessing


def is_high_usage(users_dict):
    """
    Watches for high usage and returns whether appropriate action needs to be
    taken if high usage has been detected.

    user_dict: {}
        A dictionary of User(), identified by their uid.
    """
    total_cpu, total_mem = get_usage_totals(users_dict)
    cpu_count = multiprocessing.cpu_count()
    if cfg.high_usage_watcher.div_cpu_thresholds_by_threads_per_core:
        cpu_count /= cinfo.threads_per_core
    cpu_threshold = cfg.high_usage_watcher.cpu_usage_threshold * cpu_count

    return (
        total_cpu > cpu_threshold * 100
        or total_mem > cfg.high_usage_watcher.mem_usage_threshold * 100
    )


def get_usage_totals(users_dict):
    """
    Returns a tuple of the total cpu and memory usage of users.

    users_dict: {int: user.User()}
        A dictionary of User(), identified by their uid.
    """
    total_cpu = sum(user_obj.cpu_usage for user_obj in users_dict.values())
    total_mem = sum(user_obj.mem_usage for user_obj in users_dict.values())
    return total_cpu, total_mem


def get_high_usage_users(users_dict):
    """
    Returns a list of high usage users.

    users_dict: {}
        A dictionary of User(), identified by their uid.
    """
    return sorted(
        users_dict.values(),
        reverse=True,
        key=lambda u: u.cpu_usage + u.mem_usage
    )[:cfg.high_usage_watcher.user_count]
