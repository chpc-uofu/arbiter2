"""
A module that defines actions to be taken against users.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
import threading
import socket
import collections
import copy
import time
import os
import plots
import logging
import datetime
import cinfo
import statuses
import integrations
from cfgparser import cfg

logger = logging.getLogger("arbiter." + __name__)


def prepare_warning_email(uid, username, realname, status_group, hostname,
                          table, badness_timestamp, severity="new"):
    """
    Prepare an email message to warn users about their usage policy
    violations. Returns a tuple containing the subject line and message.

    uid: str
        The user's uid.
    username: str
        The user's username.
    realname: str
        The real name of the user.
    status_group: str
        The new status group of the user.
    hostname: str
        The host on which the event took place.
    table: str
        The usage table.
    badness_timestamp: int
        A epoch timestamp of when the user starting being bad.
    severity: str
        The severity of the event (used in email titles).
    """
    # Prepare a subject line
    subject = integrations.warning_email_subject(hostname, severity, username,
                                                 realname)

    # Prepare a message body
    time_in_state = statuses.lookup_status_prop(status_group).timeout / 60
    curr_cpu_quota, curr_mem_quota = statuses.lookup_quotas(uid, status_group)
    default_status_group = statuses.lookup_default_status_group(uid)
    default_cpu_quota, default_mem_quota = statuses.lookup_quotas(
        uid,
        default_status_group
    )
    num_cores = round(default_cpu_quota / 100, 1)
    core_text = "core" if num_cores == 1 else "cores"
    message = integrations.warning_email_body(
        table,
        username,
        realname,
        hostname,
        time.strftime("%H:%M on %m/%d", time.localtime(badness_timestamp)),
        status_group,
        round(curr_cpu_quota / default_cpu_quota * 100),
        str(num_cores) + " " + core_text,
        round(time_in_state),
        round(curr_mem_quota / default_mem_quota * 100),
        round(cinfo.pct_to_gb(default_mem_quota), 1)
    )
    return subject, message


def prepare_nice_email(uid, username, realname, status_group, hostname,
                       timestamp):
    """
    Prepare an email message to notify users when their penalty status has
    timed out and are back to their default status group. Returns a tuple
    containing the subject line and message.

    uid: int
        The user's uid.
    username: str
        The user's username.
    realname: str
        The real name of the user.
    status_group: str
        The new status group of the user.
    hostname: str
        The host on which the event took place.
    timestamp: str
        The timestamp associated with the event.
    """
    # Prepare a subject line
    subject = integrations.nice_email_subject(
        hostname,
        username,
        realname,
        status_group
    )

    # Prepare a message body
    message = integrations.nice_email_body(
        username,
        realname,
        status_group,
        time.strftime("%H:%M on %m/%d", time.localtime(timestamp))
    )
    return subject, message


def prepare_high_usage_email(top_users, total_cpu_usage, total_mem_usage,
                             iso_timestamp):
    """
    Prepares an email message to notify admins that there is high usage on the
    node.

    top_users: []
        A list of the top User()s that are using the most of the machine.
    total_cpu_usage: float
        The total CPU usage on the machine.
    total_mem_usage: float
        The total memory usage on the machine.
    iso_timestamp: str
        A timestamp for the event in ISO format.
    """
    hostname = socket.gethostname()
    subject = integrations.overall_high_usage_subject(hostname)

    # Machine data
    total_mem = round(cinfo.bytes_to_gb(cinfo.total_mem))
    threads_per_core = cinfo.threads_per_core
    total_cores = os.cpu_count() / threads_per_core
    total_swap_usage = (1 - cinfo.free_swap() / cinfo.total_swap) * 100
    total_swap_gb = round(cinfo.bytes_to_gb(cinfo.total_swap))
    thread_string = "thread" if threads_per_core == 1 else "threads"

    # Prepare the message body
    message = integrations.overall_high_usage_body(
        hostname,
        iso_timestamp,
        total_cores,
        threads_per_core,
        thread_string,
        total_mem,
        total_swap_gb,
        total_cpu_usage,
        total_mem_usage,
        total_swap_usage,
        top_users
    )

    return subject, message


def send_high_usage_email(top_users, total_cpu_usage, total_mem_usage, to,
                          sender):
    """
    Sends a high usage warning email to notify admins that there is high usage
    on the node that is a result of multiple users rather than a couple/single
    users.

    top_users: []
        A list of the top User()s that are using the most of the machine.
    total_cpu_usage: float
        The total CPU usage on the machine.
    total_mem_usage: float
        The total memory usage on the machine.
    to: str, list
        The email addresses of the message recipients.
    sender: str
        The sender's email address.
    """
    timestamp = int(time.time())
    iso_timestamp = plots._iso_from_epoch(timestamp)
    subject, message = prepare_high_usage_email(top_users, total_cpu_usage,
                                                total_mem_usage, iso_timestamp)
    send_email(subject, message, to, [], sender)


def send_violation_email(subject, message_body, to, bcc, sender,
                         image_filepath=""):
    """
    Sends a violation email message.

    subject: str
        The subject of the message.
    message_body: str
        The body of the message.
    to: str, list
        The email addresses of the message recipients.
    sender: str
        The sender's email address.
    image_filepath: str
        An (optional) image to attach.
    """
    if cfg.general.debug_mode:
        # Add the current options to email messages
        # Specify variable names to include them in debug_mode emails
        debug_head = """<h2>Debug information</h2>
            <p><em>The script is currently not making changes to user quotas or
                   sending emails to users.</em></p>"""
        wanted_fields = [
            ("arbiter_refresh", cfg.general.arbiter_refresh),
            ("history_per_refresh", cfg.general.history_per_refresh),
            ("cpu_badness_threshold", cfg.badness.cpu_badness_threshold),
            ("mem_badness_threshold", cfg.badness.mem_badness_threshold),
            ("time_to_max_bad", cfg.badness.time_to_max_bad),
            ("time_to_min_bad", cfg.badness.time_to_min_bad),
        ]
        debug_body = "<br>".join([
            "{}: {}".format(field, value) for field, value in wanted_fields
        ])
        message_body = debug_head + debug_body + "<br>" + message_body

    additional_text = ""
    if cfg.processes.whitelist_other_processes:
        additional_text = " These processes are whitelisted as defined above."
    if image_filepath:
        message_body += ("""
            <h2>Recent system usage</h2><img src="cid:{}">
            <p><em>*This process is generally permitted on interactive nodes
                and is only counted against you when considering memory usage
                (regardless of the process, too much memory usage is still
                considered bad; it cannot be throttled like CPU). The process
                is included in this report to show usage holistically.</em></p>
            <p><em>**This accounts for the difference between the overall usage
                and the collected PID usage (which can be less accurate). This
                may be large if there are a lot of short-lived processes (such
                as compilers or quick commands) that account for a significant
                fraction of the total usage.{}</em></p>
        """).format(image_filepath, additional_text)
        message_body += "</em></p>"

    send_email(subject,
               message_body,
               to,
               bcc,
               sender,
               image_filepath,
               localhost=True if to and "@localhost" in to else False,
               reply_to=cfg.email.reply_to)


def send_email(subject, html_message, to, bcc, sender, image_attachment=None,
               localhost=False, reply_to=""):
    """
    Sends a given (HTML) email.

    subject: str
        The subject of the email.
    html_message: str
        The body of the email, html or plaintext.
    to: iter
        The email addresses of the message recipients as a iter of strings.
    sender: str
        The sender's email address.
    image_attachment: str, None
        An optional image location to be attached. A attached image can be
        inserted in the actual body of the text by adding a
        "<img src='cid:name'>".
    localhost: bool
        Attempts to send mail on the same host.
    reply_to: str
        The reply-to email address to be specified in the headers.
    """
    email = MIMEMultipart()

    # Attach image
    if image_attachment:
        try:
            with open(image_attachment, "rb") as image_file:
                email_image = MIMEImage(image_file.read())

            email_image.add_header("Content-ID",
                                   "<{}>".format(image_attachment))
            email.attach(email_image)
            if not cfg.email.keep_plots:
                os.remove(image_attachment)
        except (FileNotFoundError, OSError):
            logger.debug(image_attachment)
            logger.warning("Image could not be found or attached to email.")

    text = MIMEText(html_message, "html")
    email.attach(text)

    # Prepare a message with provided content
    email["Subject"] = subject
    email["From"] = sender
    if reply_to:
        email["Reply-to"] = reply_to
    if bcc:
        email["bcc"] = ", ".join(bcc)
    if to:
        email["To"] = ", ".join(to)

    # Send the message
    if email["bcc"] or email["to"]:
        mail_server = cfg.email.mail_server if not localhost else "localhost"
        smtp = smtplib.SMTP(mail_server)
        smtp.send_message(email)
        smtp.quit()


def limit_user(cgroup, limit_on, limit, fallback_limit, memsw=False):
    """
    Limits a cgroup based on the limit_on (either "cpu" or "mem") and the
    corresponding limit. If the limit specified fails to be be applied, the
    given fallback limit is then applied (if possible). Note that applying a
    memory limit causes the function to scale the resulting limit between the
    given limit and fallback_limit, such that the memory limit is as close to
    the given limit as possible. Returns whether the given limit was written
    out (e.g. if mem is scaled, returns False).

    cgroup: SystemdCGroup()
        A cgroup object that belongs to a specific group.
    limit_on: "cpu" or "mem"
        The type of limit.
    limit: int
        The limit to apply to the type of limit.
    fallback_limit: int
        The limit to apply if the original limit cannot be applied (or to
        scale back to if applying a mem limit).
    memwsw: bool
        Whether to use memsw if applying a mem limit.

    >>> # Limits memory of the uid 1001 to as much as 50% of the total memory.
    >>> limit_user(cinfo.UserSlice(1001), "mem", 50)
    True
    """
    if cfg.general.debug_mode:
        logger.debug("Not setting %s %s because debug mode is on.",
                     round(limit, 2), limit_on)
        return False
    try:
        if limit_on == "mem":
            return _scale_mem_quota(cgroup, limit, fallback_limit,
                                    memsw=memsw,
                                    retries=5,
                                    retry_rate=0.1)
        elif limit_on == "cpu":
            cgroup.set_cpu_quota(limit)
            return True
    except FileNotFoundError:
        logger.info("User: disappeared before any limit could be set. User "
                    "%s's database record will not be updated to reflect the "
                    "change.", cgroup.uid)
    except OSError as e:
        logger.warning("Failed to set a %s limit of %s%% for %s, due to an "
                       "OSError! %s", limit_on, limit, cgroup.name, e)
    return False


def _scale_mem_quota(cgroup, aimed_limit, fallback_limit, memsw=False,
                     retries=10, retry_rate=0.2):
    """
    Writes a cgroup memory limit out and retries a number of times to get as
    close as possible to the aimed_limit from the fallack_limit. After each
    retry, a period of time is waited. Returns whether the aimed_limit was
    applied.

    cgroup: SystemdCGroup()
        A cgroup object that belongs to a specific group.
    aimed_limit: int
        The limit to aim for when applying quotas.
    fallback_limit: int
        The limit to apply if the original limit cannot be applied. The limit
        will be scaled up to this if the aimed_limit fails.
    memwsw: bool
        Whether or not to use memsw.
    retries: int
        The number of times to scale the memory to the fallback limit.
    """
    limit = aimed_limit
    scale = (fallback_limit - aimed_limit) / retries
    failed_exception = ""

    # Retry a number of times
    resulting_limit = -1
    for _ in range(0, retries):
        try:
            cgroup.set_mem_quota(limit, memsw)
            resulting_limit = limit
            break
        # The limit is too low or the user disappeared
        except (OSError, FileNotFoundError) as e:
            limit += scale
            failed_exception = e
            time.sleep(retry_rate)
            continue
    if resulting_limit == -1:
        logger.debug("Failed to write out the aimed limit (%.1f%%) and the "
                     "fallback memory limit (%.1f%%)!", aimed_limit,
                     fallback_limit)
        logger.debug(failed_exception)
    elif resulting_limit == fallback_limit and fallback_limit != aimed_limit:
        logger.debug("Failed to scale the memory quota of %s to %.1f%%. A "
                     "fallback limit of %.1f%% was applied", cgroup.name,
                     aimed_limit, fallback_limit)
    elif resulting_limit == aimed_limit:
        logger.debug("Successfully set the memory quota of %s to %.1f%%",
                     cgroup.name, resulting_limit)
        return True
    else:
        logger.debug("Successfully scaled the memory quota of %s to %.1f%% "
                     "from a goal of %.1f%% based on a fallback limit of "
                     "%.1f%%", cgroup.name, limit, aimed_limit, fallback_limit)
    return False


def upgrade_penalty(user_slice, status):
    """
    Upgrades the penalty of the user, increasing their occurrences. Their
    occurrence maps directly to the order in which penalties are specified in
    the config.
    i.e., penalty1 -> penalty2 (occurrences 1 -> 2) and admin -> penalty1.

    user_slice: cinfo.UserSlice()
        The user's cgroup object.
    status: statuses.Status()
        The user's current status information.

    >>> upgrade_penalty(cinfo.UserSlice(uid=1001), statuses.get_status(1001))
    >>> statuses.get_status(1001)  # Now they are in penalty2
    ["penalty2", "normal", 1, 1534261840]
    """
    penalties = cfg.status.penalty.order
    new_occurrences = min(status.occurrences + 1, len(penalties))
    penalty_group = penalties[new_occurrences - 1]

    delta_occur = 0
    if new_occurrences != status.occurrences:  # Cap occurrences to max penalty
        delta_occur = 1
    update_status(user_slice, penalty_group, status.default)
    if statuses.in_status_file(user_slice.uid):
        statuses.update_occurrences(user_slice.uid, delta_occur,
                                    update_timestamp=True)
    return penalty_group


def update_status(user_slice, new_status, default_status):
    """
    Applies the new_status to the user. If a status group is defined as a
    penalty group, applies the quota relative to their default status if
    specified in the config. The user is removed from the status database if
    they are in their default status with 0 occurrences.

    user_slice: cinfo.UserSlice()
        The user's cgroup object.
    new_status: str
        The new status to apply to the user.
    default_status: str
        The user's default status.
    """
    cpu_quota, mem_quota = statuses.lookup_quotas(user_slice.uid, new_status)
    default_cpu_quota, default_mem_quota = statuses.lookup_quotas(user_slice.uid, default_status)
    cpu_thread = threading.Thread(target=limit_user,
                                  args=(user_slice, "cpu", cpu_quota,
                                        default_cpu_quota))
    mem_thread = threading.Thread(target=limit_user,
                                  args=(user_slice, "mem", mem_quota,
                                        default_mem_quota, cfg.processes.memsw))
    cpu_thread.start()
    mem_thread.start()

    # Add the user to the status database
    statuses.add_user(user_slice.uid, new_status, default_status)

    new_statuses = statuses.get_status(user_slice.uid)
    curr_occurrences = new_statuses[2]
    in_default_status = new_status == default_status

    if curr_occurrences == 0 and in_default_status:
        # Remove user from database
        statuses.remove_user(user_slice.uid)


def user_nice_email(user, new_status):
    """
    Sends a nice email to the user indicating that they have been released
    from penalty.
    """
    metadata = integrations.get_user_metadata(user.uid)
    # Prepare an all-clear message if the user is good
    subject, message = prepare_nice_email(
        user.uid,
        metadata.username,
        metadata.realname,
        new_status,
        socket.gethostname(),
        int(time.time())
    )
    to = [metadata.email_addr]
    bcc = cfg.email.admin_emails

    # Don't send the email to the user if debug mode is enabled
    if cfg.general.debug_mode:
        to = cfg.email.admin_emails
        bcc = ()

    send_email(
        subject,
        message,
        to,
        bcc,
        cfg.email.from_email,
        localhost=True if to and "@localhost" in to else False,
        reply_to=cfg.email.reply_to
    )


def user_warning_email(user, new_status):
    """
    Warns the user about their policy violations in a email.
    """
    metadata = integrations.get_user_metadata(user.uid)
    username = metadata.username
    # Get the expression to be used to describe the penalty status
    severity_expression = statuses.lookup_status_prop(new_status).expression

    # Get the user's baseline status
    default_status_group = statuses.lookup_default_status_group(user.uid)
    cpu_quota, mem_quota = statuses.lookup_quotas(user.uid, default_status_group)
    mem_quota_gb = cinfo.pct_to_gb(mem_quota)

    # Convert mem pcts to gb for each process
    hist = history_mem_to_gb(user.history)

    # Creates a dict of times, with a value of a list of processes per time
    events = {e["time"]: list(e["pids"].values()) for e in hist}

    # Get the same top processes in each event
    cutoff = 16
    top_events = copy.deepcopy({event: sorted(procs, reverse=True)[:cutoff]
                                for event, procs in events.items()})
    add_process_count(top_events)

    plot_filepath = os.path.join(cfg.email.plot_location, "_".join([
        datetime.datetime.today().isoformat(),
        username,
        cfg.email.plot_suffix
    ])) + ".png"
    # Generate plot
    gen_plot(plot_filepath, username, top_events, hist, mem_quota_gb,
             cpu_quota)

    email_table = generate_table(top_events, cpu_quota, mem_quota_gb)
    # Prepare the email
    subject, message = prepare_warning_email(
        user.uid,
        username,
        metadata.realname,
        new_status,
        socket.gethostname(),
        email_table,
        user.badness_timestamp,
        severity=severity_expression
    )
    to = [metadata.email_addr]
    bcc = cfg.email.admin_emails

    # Don't send the email to the user if debug mode is enabled
    if cfg.general.debug_mode:
        to = cfg.email.admin_emails
        bcc = []

    send_violation_email(subject, message, to, bcc, cfg.email.from_email,
                         plot_filepath)


def gen_plot(plot_filepath, username, proc_events, history, mem_quota_gb,
             cpu_quota):
    """
    Generates a process usage plot image in the location specified in the
    config.
    """
    # cgroup data usage
    timestamps = [event["time"] for event in history]
    cgroup_mem = [event["mem"] for event in history]
    cgroup_cpu = [event["cpu"] for event in history]
    general_events = (timestamps, cgroup_cpu, cgroup_mem)
    plots.multi_stackplot_from_procs(
        plot_filepath,
        socket.gethostname(),
        username,
        proc_events,
        general_events,
        mem_quota_gb * 1.2,  # Show some extra space above the quota
        cpu_quota * 1.2,
        cfg.badness.mem_badness_threshold * mem_quota_gb,
        cfg.badness.cpu_badness_threshold * cpu_quota
    )


def history_mem_to_gb(history):
    """
    Returns a new history item with the process and memory data converted to
    GB, rather than a pct.
    """
    new_hist = copy.deepcopy(history)
    for event in new_hist:
        event["mem"] = cinfo.pct_to_gb(event["mem"])
        for process in event["pids"].values():
            process.usage["mem"] = cinfo.pct_to_gb(process.usage["mem"])
    return new_hist


def _get_top_processes(sorted_events, cpu_quota, mem_quota_gb, cutoff=None):
    """
    Returns a list of the top processes from the sorted relevant events.

    sorted_events: {int: [StaticProcess(), ], }
        A dictionary of lists of StaticProcess()s, indexed by their event
        timestamp.
    cpu_quota: float
        The cpu quota.
    mem_quota: float
        The memory quota.
    cutoff: int, None
        The number of top processes per event desired.
    """
    ProcUsage = collections.namedtuple("ProcUsage", "name cpu_usage mem_usage")
    quotas = {"cpu": cpu_quota, "mem": mem_quota_gb}
    proc_overall_usage = collections.defaultdict(
        lambda: {"mem": 0, "cpu": 0, "n": 0}
    )

    # Combine process usage based on a process name
    for processes in sorted_events.values():
        procs_in_this_event = collections.defaultdict(
            lambda: {"mem": 0, "cpu": 0}
        )
        for process in processes:
            for metric, usage in process.usage.items():
                procs_in_this_event[process.name][metric] += usage

        # Increase the count of events a process is in
        for proc_name in procs_in_this_event:
            proc_overall_usage[proc_name]["n"] += 1

        for proc_name, usages in procs_in_this_event.items():
            for metric, usage in usages.items():
                proc_overall_usage[proc_name][metric] += usage

    # Get the average usage values over all the events
    avg_proc_overall_usage = collections.defaultdict(
        lambda: {"mem": 0, "cpu": 0}
    )
    for proc_name, proc_usages in proc_overall_usage.items():
        n = proc_overall_usage[proc_name].pop("n", 1)
        for metric, usage in proc_usages.items():
            avg_proc_overall_usage[proc_name][metric] = usage / n

    top_processes = sorted(
        avg_proc_overall_usage,
        key=lambda proc_name: sum(
            value / quotas[metric]  # Based on how close to a quota
            for metric, value in avg_proc_overall_usage[proc_name].items()
        ),
        reverse=True
    )[:cutoff]
    return [ProcUsage(
        process,
        avg_proc_overall_usage[process]["cpu"],
        avg_proc_overall_usage[process]["mem"]
    ) for process in top_processes]


def generate_table(sorted_events, cpu_quota, mem_quota_gb):
    """
    Generates an HTML table from all the processes in the sorted_events.

    sorted_events: {int: [StaticProcess(), ], }
        A dictionary of lists of StaticProcess()s, indexed by their event
        timestamp.
    cpu_quota: float
        The cpu quota.
    mem_quota_gb: float
        The memory quota in gigabytes.
    """
    top_procs = _get_top_processes(sorted_events, cpu_quota, mem_quota_gb, cutoff=15)

    # Generate a table to put in emails
    table = ("""
        <table>
            <tr>
                <td>Process</td>
                <td>Average core usage (%)</td>
                <td>Average memory usage (GB)</td>
            </tr>
    """)

    for proc in top_procs:
        table += ("""
            <tr>
                <td>{}</td>
                <td>{:0.2f}</td>
                <td>{:0.2f}</td>
            </tr>
        """).format(proc.name, proc.cpu_usage, proc.mem_usage)
    return table + "</table>"


def combine_processes(processes):
    """
    Combines the given processes together into new StaticProcess()s if they
    have the same name. Returns a iterable of processes.

    processes: iter
        A iterable of static processes.
    """
    new_processes = collections.defaultdict(lambda: 0)
    for process in processes:
        new_processes[process.name] += process
    return new_processes.values()


def add_process_count(events):
    """
    Adds a min and max count of how many processes were collected at any event
    to each process in events. If the min and max are the same, a single value
    is added. e.g. bash -> bash (1-4), top -> top (1)

    events: {int: [StaticProcess(), ], }
        A dictionary of lists of StaticProcess()s, indexed by their event
        timestamp.
    """
    # Get max and min
    inf = float("inf")
    process_extrema = collections.defaultdict(lambda: (inf, -inf))
    for processes in map(combine_processes, events.values()):
        for process in processes:
            min_count, max_count = process_extrema[process.name]
            process_extrema[process.name] = (
                min(min_count, process.count), max(max_count, process.count)
            )
    # Mark max and min
    for processes in events.values():
        for process in processes:
            process.name += " ({})".format(
                "-".join(map(str, sorted(set(process_extrema[process.name]))))
            )
