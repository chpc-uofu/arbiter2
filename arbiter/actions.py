# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
# SPDX-FileCopyrightText: Copyright (c) 2020 Idaho National Laboratory
#
# SPDX-License-Identifier: GPL-2.0-only

"""
A module that defines actions to be taken against users.
"""

from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import collections
import datetime
import itertools
import logging
import os
import smtplib
import threading
import time

import plots
from cfgparser import shared, cfg
import sysinfo
import pidinfo
import statuses
import usage
import integrations

logger = logging.getLogger("arbiter." + __name__)


def send_warning_email(user_obj, metadata, status_group, table, badness_timestamp,
                       severity, plot_filepath, syncing_hosts):
    """
    Sends a warning email message.

    user_obj: user.User()
        The user to send the email to.
    metadata: Metadata()
        A namedtuple with email_addr, username and realname values.
    status_group: str
        The new status group of the user.
    table: str
        The usage table.
    badness_timestamp: int
        A epoch timestamp of when the user starting being bad.
    severity: str
        The severity of the event (used in email titles).
    plot_filepath: str
        A path to the plot.
    syncing_hosts: [str, ]
        A set of hosts that statusdb is syncing with.
    """
    username, realname, email_addr = metadata
    to = [email_addr]
    bcc = cfg.email.admin_emails
    subject = integrations.warning_email_subject(sysinfo.hostname, severity,
                                                 username, realname)

    time_in_penalty = statuses.lookup_status_prop(status_group).timeout / 60
    penalty_cpu_quota, penalty_mem_quota = user_obj.status.quotas()
    cpu_quota, mem_quota = user_obj.status.quotas(default=True)
    num_cores = round(cpu_quota / 100, 1)
    core_text = "core" if num_cores == 1 else "cores"
    message_body = integrations.warning_email_body(
        table,
        username,
        realname,
        sysinfo.hostname,
        time.strftime("%H:%M on %m/%d", time.localtime(badness_timestamp)),
        status_group,
        round(penalty_cpu_quota / cpu_quota * 100),
        str(num_cores) + " " + core_text,
        round(time_in_penalty),
        round(penalty_mem_quota / mem_quota * 100),
        round(sysinfo.pct_to_gb(mem_quota), 1),
        syncing_hosts,
    )

    if cfg.general.debug_mode:
        to = cfg.email.admin_emails
        bcc = []
        debug_head = """
            <h2>Debug information</h2>
            <p><em>The script is currently not making changes to user quotas or
                   sending emails to users.</em></p>
        """
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
    if plot_filepath:
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
        """).format(plot_filepath, additional_text)
        message_body += "</em></p>"

    send_email(
        subject,
        message_body,
        to,
        bcc,
        cfg.email.from_email,
        plot_filepath,
        localhost=True if to and "@localhost" in to else False,
        reply_to=cfg.email.reply_to
    )


def send_nice_email(metadata, status_group):
    """
    Prepare an email message to notify users when their penalty status has
    timed out and are back to their default status group.

    metadata: Metadata()
        A namedtuple with email_addr, username and realname values.
    status_group: str
        The new status group of the user.
    """
    username, realname, email_addr = metadata
    subject = integrations.nice_email_subject(
        sysinfo.hostname,
        username,
        realname,
        status_group
    )
    message = integrations.nice_email_body(
        username,
        realname,
        status_group,
        time.strftime("%H:%M on %m/%d", time.localtime(int(time.time())))
    )
    to = [email_addr]
    bcc = cfg.email.admin_emails
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


def send_high_usage_email(top_users, total_cpu_usage, total_mem_usage):
    """
    Sends an email message to notify admins that there is high usage on the
    node.

    top_users: []
        A list of the top User()s that are using the most of the machine.
    total_cpu_usage: float
        The total CPU usage on the machine.
    total_mem_usage: float
        The total memory usage on the machine.
    """
    hostname = sysinfo.hostname
    subject = integrations.overall_high_usage_subject(hostname)
    timestamp = int(time.time())
    epoch_datetime = datetime.datetime.fromtimestamp(timestamp)
    iso_timestamp = datetime.datetime.isoformat(epoch_datetime)

    # Machine data
    total_mem = round(sysinfo.bytes_to_gb(sysinfo.total_mem))
    threads_per_core = sysinfo.threads_per_core
    total_cores = os.cpu_count() / threads_per_core
    total_swap_usage = (1 - sysinfo.free_swap() / sysinfo.total_swap) * 100
    total_swap_gb = round(sysinfo.bytes_to_gb(sysinfo.total_swap))
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
    send_email(subject, message, cfg.email.admin_emails, [], cfg.email.from_email)


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
        Attempts to send mail to local users on the machine (/var/spool).
        Requires that all the recipients end with a "@localhost".
        e.g. "username@localhost"
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
        except OSError as err:
            logger.debug("%s: %s", image_attachment, err)
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
        try:
            with smtplib.SMTP(mail_server) as smtp:
                smtp.send_message(email)
        except Exception as err:
            logger.debug(err)
            logger.warning("Unable to send message: %s", str(err))


def limit_user(user_obj, limit_on, limit, fallback_limit, memsw=False):
    """
    Limits a user slice based on the limit_on (either "cpu" or "mem") and the
    corresponding limit. If the limit specified fails to be be applied, the
    given fallback limit is then applied (if possible). Note that applying a
    memory limit causes the function to scale the resulting limit between the
    given limit and fallback_limit, such that the memory limit is as close to
    the given limit as possible. Returns whether the given limit was written
    out (e.g. if mem is scaled, returns False).

    user_obj: user.User()
        A user to limit.
    limit_on: "cpu" or "mem"
        The type of limit.
    limit: int
        The limit to apply to the type of limit.
    fallback_limit: int
        The limit to apply if the original limit cannot be applied (or to
        scale back to if applying a mem limit).
    memwsw: bool
        Whether to use memsw if applying a mem limit.

    >>> # Limits memory of the uid 1001 to at least 50% of the total memory.
    >>> limit_user(cginfo.UserSlice(1001), "mem", 50)
    True
    """
    try:
        if limit_on == "mem":
            return _scale_mem_quota(user_obj.cgroup, limit, fallback_limit,
                                    memsw=memsw,
                                    retries=5,
                                    retry_rate=0.1)
        elif limit_on == "cpu":
            user_obj.cgroup.set_cpu_quota(limit)
            logger.debug("Successfully set the CPU quota of %s to %.1f%%",
                         user_obj.uid_name, limit)
            return True
    except FileNotFoundError:
        logger.info("User: disappeared before any limit could be set. User "
                    "%s's database record will not be updated to reflect the "
                    "change.", user_obj.uid_name)
    except OSError as err:
        logger.warning("Failed to set a %s limit of %s%% for %s, due to an "
                       "OSError: %s", limit_on, limit, user_obj.uid_name, err)
    return False


def _scale_mem_quota(cgroup, aimed_limit, fallback_limit, memsw=False,
                     retries=10, retry_rate=0.2):
    """
    Writes a cgroup memory limit out and retries a number of times to get as
    close as possible to the aimed_limit from the fallack_limit. After each
    retry, a period of time is waited. Returns whether the aimed_limit was
    applied.

    cgroup: cginfo.SystemdCGroup()
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
    retry_rate: float
        The rate at which to wait between retries.
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
        # The limit is too low or the cgroup disappeared
        except OSError as err:
            limit += scale
            failed_exception = err
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


def set_quotas(user_obj):
    """
    Applies the quotas of the new status group to user. If the quotas are
    already set, then nothing happens.

    user_obj: user.User()
        A user to update the status of.
    """
    uid = user_obj.uid
    memsw = cfg.processes.memsw
    status = user_obj.status
    cgroup = user_obj.cgroup
    cpu_quota, mem_quota = status.quotas()
    default_cpu_quota, default_mem_quota = status.quotas(default=True)

    try:
        # mostly_eq() because we can't set a lower memory limit (e.g. putting
        # someone in penalty) than a cgroup already has, and our subsequent
        # attempts to set this limit lower will fail quite often. It's enough
        # to call it done if it's mostly equal
        eq_cpu_quota = mostly_eq(cpu_quota, cgroup.cpu_quota())
        eq_mem_quota = mostly_eq(mem_quota, sysinfo.bytes_to_pct(cgroup.mem_quota(memsw)))
    except FileNotFoundError:
        return
    except OSError as err:
        logger.debug("Failed to get quotas because %s", err)
        return

    if eq_cpu_quota and eq_mem_quota:
        return

    logger.debug("Applying limits for %s", uid)
    if not eq_cpu_quota:
        cpu_thread = threading.Thread(
            target=limit_user,
            args=(user_obj, "cpu", cpu_quota, default_cpu_quota)
        )
        cpu_thread.start()
    if not eq_mem_quota:
        mem_thread = threading.Thread(
            target=limit_user,
            args=(user_obj, "mem", mem_quota, default_mem_quota, memsw)
        )
        mem_thread.start()


def mostly_eq(lvalue, rvalue, fudge=0.05):
    """
    Returns whether the two values are mostly equal based on the fudge factor.

    lvalue: float
        The value on the left.
    rvalue: float
        The value on the right.
    fudge: float
        The margin of error to account for. i.e. the fudge factor
    """
    return lvalue >= rvalue * (1 - fudge) and lvalue <= rvalue * (1 + fudge)


def user_nice_email(uid, new_status_group):
    """
    Sends a nice email to the user indicating that they have been released
    from penalty.

    uid: int
        A uid of the user.
    new_status_group: str
        The new status group to that has been applied to the user.
    """
    metadata = integrations.get_user_metadata(uid)
    send_nice_email(metadata, new_status_group)


def user_warning_email(user_obj, new_status_group, syncing_hosts):
    """
    Warns the user about their policy violations in a email.

    user_obj: user.User()
        The user to send the email to.
    new_status_group: str
        The new status group to that has been applied to the user.
    syncing_hosts: [str, ]
        A list of hosts that statusdb is syncing with.
    """
    uid = user_obj.uid
    metadata = integrations.get_user_metadata(uid)
    username = metadata.username
    # Get the expression to be used to describe the penalty status
    severity_expression = statuses.lookup_status_prop(new_status_group).expression

    # Get the user's baseline status
    cpu_quota, mem_quota = user_obj.status.quotas(default=True)
    mem_quota_gb = sysinfo.pct_to_gb(mem_quota)

    # Convert mem pcts to gb for each process
    hist = history_mem_to_gb(user_obj.history_iter())

    # Creates a dict of times, with a value of a list of processes per time
    events = {e["time"]: list(e["pids"].values()) for e in hist}

    # We don't want the graph to overflow, so we'll cap things here
    plot_proc_cap = cfg.email.plot_process_cap
    top_events = cap_procs_in_events(events, cpu_quota, mem_quota_gb,
                                     plot_proc_cap)
    add_process_count(top_events)

    plot_filepath = os.path.join(cfg.email.plot_location, "_".join([
        datetime.datetime.today().isoformat(),
        username,
        cfg.email.plot_suffix
    ])) + ".png"
    # Generate plot
    generate_plot(plot_filepath, username, top_events, hist, cpu_quota,
                  mem_quota_gb)

    table_proc_cap = cfg.email.table_process_cap
    email_table = generate_table(top_events, cpu_quota, mem_quota_gb,
                                 table_proc_cap)
    send_warning_email(
        user_obj,
        metadata,
        new_status_group,
        email_table,
        user_obj.badness_obj.start_of_badness(),
        severity_expression,
        plot_filepath,
        syncing_hosts
    )


def cap_procs_in_events(events, cpu_quota, mem_quota_gb, cap):
    """
    Returns a new event dictionary with the number of processes over all the
    events being capped at the given cutoff based on the usage relative to cpu
    and memory quotas. The missing usage is ignored.

    events: {int: [StaticProcess(), ], }
        A dictionary of lists of StaticProcess()s, indexed by their event
        timestamp.
    cpu_quota: float
        The cpu quota.
    mem_quota_gb: float
        The memory quota in gigabytes.
    cap: int
        The max number of processes over all the events.
    """
    uniq_summed_procs_per_event = (
        pidinfo.combo_procs_by_name(procs) for procs in events.values()
    )
    uniq_summed_procs = pidinfo.combo_procs_by_name(
        itertools.chain.from_iterable(uniq_summed_procs_per_event)
    )
    sorted_procs = usage.rel_sorted(
        uniq_summed_procs,
        cpu_quota, mem_quota_gb,
        key=lambda p: (p.usage["cpu"], p.usage["mem"]),  # cpu, memory
        reverse=True
    )
    # Always include "other processes**"
    top_proc_names = {
        proc.name
        for i, proc in enumerate(sorted_procs)
        if i < cap and proc.name != shared.other_processes_label
    }
    return {
        event: [proc for proc in procs if proc.name in top_proc_names]
        for event, procs in events.items()
    }


def generate_plot(plot_filepath, username, proc_events, history, cpu_quota,
                  mem_quota_gb):
    """
    Generates a process usage plot image in the location specified in the
    config.

    plot_filepath: str
        A path to the plot.
    username:
        The username of the user.
    proc_events: {}
        A dictionary of events; the value is a list of processes that are
        associated with that time event.
    history: collections.deque(dict, )
        A list of history events ordered chronologically (i.e. most recent is
        first). History events are formatted as:
            {"time": float,
             "mem": float,
             "cpu": float,
             "pids": {int (pid): pidinfo.StaticProcess()}}
    cpu_quota: float
        The cpu quota.
    mem_quota_gb: float
        The memory quota in gigabytes.
    """
    timestamps = [event["time"] for event in history]
    cgroup_mem = [event["mem"] for event in history]
    cgroup_cpu = [event["cpu"] for event in history]
    overall_usage = (timestamps, cgroup_cpu, cgroup_mem)
    title = "Utilization of {} on {}".format(username, sysinfo.hostname)
    plots.multi_stackplot_from_events(
        plot_filepath,
        title,
        proc_events,
        overall_usage,
        cpu_quota,
        mem_quota_gb,
        cfg.badness.cpu_badness_threshold * cpu_quota,
        cfg.badness.mem_badness_threshold * mem_quota_gb
    )


def generate_table(events, cpu_quota, mem_quota_gb, max_rows):
    """
    Generates an HTML table from all the processes in the events.

    events: {int: [StaticProcess(), ], }
        A dictionary of lists of StaticProcess()s, indexed by their event
        timestamp.
    cpu_quota: float
        The cpu quota.
    mem_quota_gb: float
        The memory quota in gigabytes.
    max_rows: int
        The max number of rows to generate.
    """
    table = ("""
        <table>
            <tr>
                <td>Process</td>
                <td>Average core usage (%)</td>
                <td>Average memory usage (GB)</td>
            </tr>
    """)

    for proc in avg_procs_over_events(events, cpu_quota, mem_quota_gb)[:max_rows]:
        table += ("""
            <tr>
                <td>{}</td>
                <td>{:0.2f}</td>
                <td>{:0.2f}</td>
            </tr>
        """).format(proc.name, proc.usage["cpu"], proc.usage["mem"])
    return table + "</table>"


def history_mem_to_gb(history_iter):
    """
    Returns a new history item with the process and memory data converted to
    GB, rather than a pct.

    history_iter: collections.deque(dict, )
        A iterator of history events ordered chronologically (i.e. most recent
        is first). History events are formatted as:
            {"time": float,
             "mem": float,
             "cpu": float,
             "pids": {int (pid): pidinfo.StaticProcess()}}
    """
    new_history = collections.deque()
    for event in history_iter:
        event["mem"] = sysinfo.pct_to_gb(event["mem"])
        for process in event["pids"].values():
            process.usage["mem"] = sysinfo.pct_to_gb(process.usage["mem"])
        new_history.appendleft(event)
    return new_history


def avg_procs_over_events(events, cpu_quota, mem_quota_gb):
    """
    Returns a list of StaticProcess()s averaged over the events.

    events: {int: [StaticProcess(), ], }
        A dictionary of lists of StaticProcess()s, indexed by their event
        timestamp.
    cpu_quota: float
        The cpu quota.
    mem_quota: float
        The memory quota.
    """
    # The idea here is to get the total usage of a processes with the same
    # name per event, sum them and then divide that usage by the range of
    # events that the process occurs in.
    #
    # For averages normally you'd divide by the total number of events,
    # however when we show that data to users it can be quite small if there
    # is a large number of history events kept (especially if
    # time_to_max_bad is a small proportion of the number of history events
    # times the arbiter_refresh * history_per_refresh), so we'll justify
    # ourselves more here by dividing by the range it appears in, not events
    uniq_summed_procs_by_event = list(map(
        pidinfo.combo_procs_by_name, events.values()
    ))
    uniq_summed_procs = set(
        pidinfo.combo_procs_by_name(
            itertools.chain.from_iterable(uniq_summed_procs_by_event)
        )
    )
    avg_procs = []
    for summed_proc in uniq_summed_procs:
        events_seen_in = [
            i
            for i, procs in enumerate(uniq_summed_procs_by_event)
            if summed_proc in procs  # StaticProcess __eq__ looks at names
        ]
        delta_range_events_seen_in = (max(events_seen_in) + 1) - min(events_seen_in)
        avg_proc = summed_proc / delta_range_events_seen_in
        avg_procs.append(avg_proc)

    return usage.rel_sorted(
        avg_procs,
        cpu_quota, mem_quota_gb,
        key=lambda proc: (proc.usage["mem"], proc.usage["cpu"]),
        reverse=True
    )


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
    for processes in map(pidinfo.combo_procs_by_name, events.values()):
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
