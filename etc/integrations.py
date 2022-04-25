from cfgparser import cfg
import pwd
import os
import collections
import logging

"""
A module used for integrating a specific site into Arbiter.
"""

logger = logging.getLogger("arbiter." + __name__)


def warning_email_subject(hostname, severity, username, realname):
    """
    Prepares a subject line for warnings about usage policy violations.

    hostname: str
        The hostname of the machine.
    severity: str
        A expression of how severe the violation is (the penalty expression).
        e.g. "new", "repeated", "severe"
    username: str
        The username of the user.
    realname: str
        The realname of the user.
    """
    subject = "[{} Arbiter2] {} violation of usage policy by {} ({})"
    return subject.format(
        hostname,
        severity.title(),
        username,
        realname
    )


def format_cluster_hostname_list(hostnames):
    """
    Returns a formatted string with comma-seperated hostnames. If multiple
    hostnames start with the same prefix and end with a digit (i.e. in the
    same cluster), the hostnames are combined into a range expansion (e.g.
    cluster[1-7]) if the digits are sequential and greater than two in the
    count, or brace expansion (e.g. cluster{1,4,5}) otherwise.

    Assumes cluster names are not prefix substrings of one another and that
    digits don't have leading 0s. e.g. cannot have both f1 and frisco1 nor
    frisco001.

    hostnames: iter
        An iterable of string hostnames to format.

    >>> format_cluster_hostname_list(["np1", "kp1", "kp2", "f1", "f2", "f3"])
    "f[1-3], kp{1,2}, and np1"
    >>> format_cluster_hostname_list(["np1", "f1", "f2", "f3"])
    "f[1-3] and np1"
    >>> format_cluster_hostname_list(["f1", "f3"])
    "f{1,3}"
    """
    uniq_hostnames = set(hostnames)  # Ensure no duplicates
    prefixes = {hostname.rstrip("0123456789") for hostname in uniq_hostnames}

    # A cluster is defined by having two or more hostnames with the same
    # prefix with different ending digits

    # Map prefixes to a set of trailing digits found in hostnames; clusters
    # will be a subset of the prefixes where > 1 digits
    prefix_digits_map = collections.defaultdict(set)
    for hostname in uniq_hostnames:
        for prefix in prefixes:
            # Note: Assumes no prefixes are leading substrings of one another
            if hostname.startswith(prefix):
                maybe_digits = hostname[len(prefix):]
                if maybe_digits.isdigit():
                    # Note: This int() is problematic for formatting with leading zeros
                    prefix_digits_map[prefix].add(int(maybe_digits))

    # Now we know the cluster names and their corresponding digits
    cluster_digits_map = {
        prefix: prefix_digits_map[prefix]
        for prefix in prefix_digits_map
        if len(prefix_digits_map[prefix]) > 1
    }
    # All the other hostnames that didn't match the cluster criteria
    not_clustered_hostnames = {
        hostname
        for hostname in uniq_hostnames
        # Note: Assumes no prefixes are leading substrings of one another
        if not any(hostname.startswith(cluster) for cluster in cluster_digits_map.keys())
    }

    formatted_hostnames = []
    for cluster, digits in cluster_digits_map.items():
        min_digit, max_digit = min(digits), max(digits)
        is_sequential = max_digit - min_digit == len(digits)-1  # ok b/c unique digits
        if len(digits) > 2 and is_sequential:
            formatted_hostnames.append("{}[{}-{}]".format(cluster, min_digit, max_digit))
        else:
            # {{ -> just one '{' with .format()
            formatted_hostnames.append("{}{{{}}}".format(cluster, ",".join(map(str, sorted(digits)))))

    formatted_hostnames.extend(not_clustered_hostnames)
    formatted_hostnames.sort()
    if len(formatted_hostnames) > 1:
        return (", ".join(formatted_hostnames[:-1]) +
                ("," if len(formatted_hostnames) > 2 else "") +  # oxford comma
                " and " + formatted_hostnames[-1])
    else:
        return formatted_hostnames[0]


def warning_email_body(proc_table, username, realname, hostname,
                       timestamp, status_group, cpu_pct, default_core_text,
                       time_in_state, mem_pct, default_mem, syncing_hosts):
    """
    Prepares the body of a warning message.

    proc_table: str
        A html table full of the the top proccesses.
    username: str
        The username of the user.
    realname: str
        The realname of the user.
    hostname: str
        The hostname of the machine.
    timestamp: str
        A nicely formatted timestamp that indicates when the user started to be
        bad.
    status_group: str
        The name of the status that the user now has (e.g. penalty1).
    cpu_pct: float
        The user's current quota, as a percent of the user's default quota.
        e.g. 80.0 -> 80% of your original limit
    default_core_text: str
        The cpu quota expressed in human terms. e.g. "4.0 core(s)"
    time_in_state: int
        How many minutes till the user is released from their status.
    mem_pct: float
        The user's current quota, as a percent of the user's default quota.
        e.g. 80.0 -> 80% of your original limit
    default_mem: str
        The mem quota expressed in human terms. e.g. "8.0 GB"
    syncing_hosts: [str, ]
        A list of hosts that Arbiter2 is syncing with.
    """
    # Prepare a message body using the template
    with open("../etc/warning_email_template.txt", "r") as template:
        message = template.read()
    message = message.format(
        username,
        realname,
        hostname,
        timestamp,
        status_group,
        cpu_pct,
        default_core_text,
        time_in_state,
        mem_pct,
        default_mem,
        format_cluster_hostname_list(syncing_hosts),
    )
    message += proc_table
    return message


def nice_email_subject(hostname, username, realname, status_group):
    """
    Prepares a subject line for messages sent when users are returned to a
    default status (from penalty).

    hostname: str
        The hostname of the machine.
    username: str
        The username of the user.
    realname: str
        The realname of the user.
    status_group: str
        The name of the status that the user now has (e.g. normal).
    """
    subject = "[{} Arbiter2] User {} ({}) has been returned to {} status"
    return subject.format(
        hostname,
        username,
        realname,
        status_group
    )


def nice_email_body(username, realname, status_group, timestamp):
    """
    Prepares the body of a return-to-normal message. This is used in the
    actions module.

    username: str
        The username of the user.
    realname: str
        The realname of the user.
    status_group: str
        The name of the status that the user now has (e.g. normal).
    timestamp: str
        A nicely formatted timestamp that indicates when the user started to be
        bad.
    """
    with open("../etc/nice_email_template.txt", "r") as template:
        message = template.read()
    return message.format(
        username,
        realname,
        status_group,
        timestamp
    )


def overall_high_usage_subject(hostname):
    """
    Prepares the subject line for an overall high usage email.

    hostname: str
        The hostname of the machine.
    """
    return("[{} Arbiter2] High overall usage".format(hostname))


def overall_high_usage_body(hostname, iso_timestamp, total_cores,
                            threads_per_core, thread_string, total_mem,
                            total_swap, total_cpu_usage, total_mem_usage,
                            total_swap_usage, top_users):
    """
    Prepares the body of the overall high usage email.

    hostname: str
        The hostname of the machine.
    iso_timestamp: str
        A timestamp for the event in ISO format.
    total_cores: int
        The number of cores on the machine.
    threads_per_core: int
        The number of threads per core.
    thread_string: str
        "thread" or "threads".
    total_mem: float
        The total mem of the machine in GB.
    total_swap: float
        The total swap space of the machine in GB.
    total_cpu_usage: float
        The total CPU usage on the machine.
    total_mem_usage: float
        The total memory usage on the machine.
    total_swap_usage: float
        The total swap usage on the machine as a pct of available swap.
    top_users: []
        A list of the top user.User() that are using the most of the machine.
    """
    with open("../etc/overall_high_usage_email_template.txt", "r") as template:
        message = template.read()
    # Prepare all the information about users
    user_text = ""
    for user in top_users:
        username, realname = _get_name(user.uid)
        user_text += ("""
            <tr>
                <td>{} ({})</td>
                <td>{:0.2f}</td>
                <td>{:0.2f}</td>
                <td>{}</td>
                <td>{:0.2f}</td>
                <td>{:0.2f}</td>
            </tr>
        """).format(
            username,
            realname,
            user.cpu_usage,
            user.mem_usage,
            "{}/{}".format(user.status.current, user.status.default),
            user.cpu_quota,
            user.mem_quota
        )
    one_la, five_la, fifteen_la = os.getloadavg()
    return message.format(
        hostname,
        iso_timestamp,
        total_cores,
        threads_per_core,
        thread_string,
        total_mem,
        total_swap,
        one_la, five_la, fifteen_la,
        total_cpu_usage,
        total_mem_usage,
        total_swap_usage,
        user_text,
        len(top_users)
    )


def email_addr_of(username):
    """
    Returns the email address of a user based on their uid or username using a
    post request to a server. If the lookup fails, None is returned.

    username: str
        The username of the user.
    """
    return email_addr_placeholder(username)


def email_addr_placeholder(username):
    """
    The default email address format (used to send things to users).

    username: str
        The username of the user.
    """
    return ("{}@" + cfg.email.email_domain).format(username)


def _get_name(uid):
    """
    Returns a tuple containing the user's username and real name. If they are
    not found, a placeholder is returned instead.

    uid: int
        The user's uid.
    """
    username = "unknown username"
    realname = "unknown real name"
    try:
        pwd_info = list(pwd.getpwuid(uid))
        if pwd_info[0].strip() != "":
            username = pwd_info[0]
        if pwd_info[4].strip() != "":
            realname = pwd_info[4].rstrip(",")
    except KeyError:
        pass
    return username, realname


def get_user_metadata(uid):
    """
    The user's metadata, such as the username, realname and their email
    address.

    uid: int
        The user's uid.
    """
    username, realname = _get_name(uid)
    UserMetadata = collections.namedtuple("UserMetadata",
                                          "username realname email_addr")
    email_addr = email_addr_of(username)
    if email_addr is None:  # If lookup fails
        logger.warning("Could not find the email address of user: {}!".format(
            uid
        ))
        if "unknown" not in username:  # Check for placeholder
            email_addr = email_addr_placeholder(username)
        else:
            logger.warning("Could not find the username or email address of "
                           "user: {}! Email to user will not sent!".format(uid))
            email_addr = ""
    return UserMetadata(username, realname, email_addr)
