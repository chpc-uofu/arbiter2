#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# A program to read log database files and extract data, such as the number of
# violations by user and the number of times a process was encountered. The
# script optionally sends an email. This can be used to periodically check the
# recent history of policy violations without filtering through individual
# email messages.
#
# Written by Robben Migacz
# Usage: ./arbreport.py (to see available arguments)
import argparse
import shlex
import os
import sys
import toml
import datetime
import html
import pwd
import grp

html_max_rows = 20

table_descriptions = [
    "<h2>Penalties by user</h2><p>This table shows the number of policy "
    "violations at each level for a given user.</p>",
    "<h2>Penalties by host</h2><p>This table shows the number of penalties "
    "(of any level) on each host.</p>",
    "<h2>Processes by occurrences in penalties</h2><p>This table shows the "
    "number of times processes were observed in penalty transitions. If a "
    "process was observed at least once in association with a penalty, it is "
    "shown here. Note that whitespace may be replaced with an underscore "
    "character and process names may be cut off. If a process was seen "
    "multiple times in association with a penalty, it is only counted "
    "once.</p>",
    "<h2>New processes by occurrences in penalties</h2><p>This table shows the "
    "number of times new processes were observed in penalty transitions. These "
    "processes have not been seen by the reporting tool before.</p>"
]

def get_user_info(uid):
    uid = int(uid)  # Ensure an integer is used as the uid (or it won't work)
    username = None
    group = None
    groupid = None
    # Try to get the username and group ID
    try:
        pwd_info = pwd.getpwuid(uid)
        username = pwd_info.pw_name
        groupid = pwd_info.pw_gid
    except:
        pass
    # Try to get the group name
    try:
        group = grp.getgrgid(groupid).gr_name
    except:
        pass
    return (str(username), str(group))


def main(
    args,
    send_email=False,
    email_to=None,
    email_from=None,
    email_subject=None,
    date_start=None,  # Can be ISO-formatted string, e.g. 2020-01-01
    date_end=None,  # Can be ISO-formatted string, e.g. 2020-01-01
    date_fallback_interval=7,  # Days to consider if no interval is specified
    logdb_name="{}",
    log_location=None,
    reply_to=None,
    process_history=None
):
    # Deal with times
    # If the user doesn't specify a very specific time (with a start and end
    # date), we need to find a time window. This involves picking an end date
    # for the interval (by default, the current date) and a start date (by
    # default, the end date less some number of days).
    if not date_start or not date_end:
        now = datetime.datetime.now()

    if not date_end:
        date_end = now
    else:
        date_end = datetime.datetime.strptime(date_end, "%Y-%m-%d")

    if not date_start:
        # Neither a start date nor a number of days is specified; fail
        if not date_fallback_interval:
            print("There was not enough information to get a date range.")
            sys.exit(2)
        start = now - datetime.timedelta(days=date_fallback_interval)
        date_start = start
    else:
        date_start = datetime.datetime.strptime(date_start, "%Y-%m-%d")

    # Get the relevant dates
    # This is done with datetime to allow dates to span multiple months
    # (i.e. let Python deal with things like leap years)
    between_dates = [date_start + datetime.timedelta(days=i)
                     for i in range((date_end - date_start).days + 1)]
    # Convert dates to ISO-formatted strings
    between_dates = [each.isoformat().split("T")[0] for each in between_dates]
    # Use the logdb name specification to get relevant filenames
    filenames = [logdb_name.format(d) for d in between_dates]

    # Crawl the logdb directory to get relevant files
    if not log_location:
        logdir = cfg.database.log_location
        # Get up to the last slash by default
        # The default behavior is to use ../logs/hostname as the directory
        # This works for arbiter/tools/ since ../logs is still a valid path,
        # but things might not work so well for other configurations or places
        # from which the script is run.
        log_location = "/".join(logdir.split("/")[:-1])

    # Read and parse each relevant file
    # Interpret and format the data from reading the files
    files_to_read = []
    walked = os.walk(log_location)
    for (directory, subdirectories, files) in walked:
        files_to_read.extend(
            [[directory, os.path.join(directory, f)]  # Keep hostname, filename
             for f in files
             if f in filenames]  # Keep only the relevant files (in date range)
        )

    # Get the list of processes that have been seen before, if relevant
    if process_history:
        try:
            with open(process_history, "r") as process_history_file:
                previous_processes = process_history_file.readlines()
                previous_processes = [each.strip() for each in previous_processes]
        except:
            print("Failed to open the process history file")
            process_history = False

    # Collect the relevant data from the files that were identified
    actions_by_user = {}
    hosts_by_user = {}
    times_proc_seen = {}
    procs_seen = []
    for pair in files_to_read:

        filename = pair[1]
        # Get the hostname
        # Assumes the hostname is the second-to-last hierarchy level for files,
        # as in the default configuration. If that's not the case for you, this
        # is probably the part you want to change.
        directory = pair[0].split(log_location)[1].replace("/", "")

        try:
            log_info = logdb.get_log_info(filename)
            actions_tab, general, processes = log_info[0], log_info[1], log_info[2]

            # Action: action, user, timestamp
            # General: mem, cpu, time
            # Process: name, mem, cpu, uptime, timestamp

            # Get users and penalties
            for each in actions_tab:
                user = each[1].user
                description = each[1].action
                # logdb files may contain high_usage_warning actions, which are
                # not applied to any specific user (rather, to the node in
                # general); we want to skip those for user-specific analyses
                if description == "high_usage_warning":
                    continue

                # Get the number of violations of each type for each user
                if user not in actions_by_user:
                    actions_by_user[user] = {}
                if description not in actions_by_user[user]:
                    actions_by_user[user][description] = 1
                else:
                    actions_by_user[user][description] += 1

                # Get the number of violations on each host for each user
                if user not in hosts_by_user:
                    hosts_by_user[user] = {}
                if directory not in hosts_by_user[user]:
                    hosts_by_user[user][directory] = 1
                else:
                    hosts_by_user[user][directory] += 1

            # Count the number of times each process name is seen in a unique
            # action (a penalty state elevation).
            keys_by_procs = {}
            for each in processes:
                primary_key = each[0]  # The primary key associates the Process
                                       # to an Action to avoid double-counting
                proc = each[1]
                if proc.name.startswith(shared.other_processes_label):
                    continue
                key = proc.name.replace(" ", "_")
                if key not in keys_by_procs:
                    keys_by_procs[key] = []
                if primary_key not in keys_by_procs[key]:
                    keys_by_procs[key].append(primary_key)
                if key and key not in procs_seen:
                    procs_seen.append(key)
            # Collapse the primary keys into a count for each process
            # This shows how many actions the process name is associated with;
            # this is necessary because there are multiple Process objects for
            # each Action object.
            for key in keys_by_procs:
                keys_by_procs[key] = len(keys_by_procs[key])
                if key not in times_proc_seen:
                    times_proc_seen[key] = 0
                times_proc_seen[key] += keys_by_procs[key]

        except:
            print(
                "Could not read database {}. It is possible the schema is not "
                "correct. Skipping.".format(filename)
            )
            continue

    # Count the number of new processes
    times_new_proc_seen = {}
    if process_history:

        new_procs = set(procs_seen).difference(previous_processes)
        times_new_proc_seen = dict([(key, times_proc_seen[key]) for key in new_procs])

        # Get the most common processes that are also new
        unsorted_list = [(key, val) for key, val in times_new_proc_seen.items()]
        sorted_result = sorted(unsorted_list, key=lambda x: x[1], reverse=True)

        new_proc_names = [each[0] for each in sorted_result]
        all_procs = previous_processes + new_proc_names[:html_max_rows]

        with open(process_history, "w") as process_history_file:
            process_history_file.write("\n".join(all_procs))

    tables = get_text_tables(
        actions_by_user,
        hosts_by_user,
        times_proc_seen,
        times_new_proc_seen=times_new_proc_seen
    )
    for table in tables:
        print(table)

    html = text_to_html(tables)

    # Send emails
    if send_email:
        # Apply a default recipient list
        if not email_to:
            email_to = cfg.email.admin_emails
        # Apply the default sender
        if not email_from:
            email_from = cfg.email.from_email
        # Apply the reply-to address
        if not reply_to:
            reply_to = cfg.email.reply_to

        # Email subject
        # This requires special care: for the default, we want to include dates
        if not email_subject:
            email_subject = "[Arbiter2] Summary report for {} to {}".format(
                between_dates[0],
                between_dates[-1]
            )

        message = html

        actions.send_email(
            email_subject,
            message,
            email_to,
            [],
            email_from,
            localhost=False,
            reply_to=reply_to
        )


def text_to_html(tables):
    """
    Convert text-based tables to HTML. Return the HTML for use in other places,
    such as emails.
    """
    overall_text = ""  # Save the table HTML
    overall_text += (
        "<em>Tables are limited to a maximum of {} rows.</em>\n"
        .format(html_max_rows)
    )
    # Add text for each table (as a separate table)
    for n, table in enumerate(tables):
        lines = table.splitlines()[:html_max_rows]
        overall_text += table_descriptions[n]
        table_text = "<table style='border-collapse: collapse;'>\n"
        for line in lines:
            table_text += "<tr>\n"  # For each line in the text table, add a row
                                  # in the HTML table
            # Add a cell for each item in the text table
            for field in line.split():
                table_text += ("<td style='border: 1px solid black; "
                               "padding: 4px;'>\n")
                table_text += field
                table_text += "</td>\n"
            table_text += "<tr>\n"
        table_text += "</table>\n"  # Close out the table
        overall_text += table_text  # Add the table to the overall HTML
    return overall_text


def get_text_tables(
    actions_by_user,
    hosts_by_user,
    times_proc_seen,
    times_new_proc_seen=None,
    str_len=20
):
    """
    Output the results of the analysis. This writes the results in plain text.
    """
    table1 = ""
    # Penalty state counts
    key_list = []
    for uid in actions_by_user:
        keys = actions_by_user[uid].keys()
        key_list.extend(keys)
    key_list = sorted(list(set(key_list)))

    # Print a text-only table for penalty state counts
    header = (
        "username".ljust(str_len)
        + "uid".ljust(str_len)
        + "group".ljust(str_len)
        + "".join([k.ljust(str_len) for k in key_list])
    )
    table1 += header + "\n"
    # Sort the table by using a list of tuples
    # The tuple is formatted as (key, value) for each item in the dictionary
    # This lets us sort the table to show the most relevant items first
    tuple_version = [(key, value) for key, value in actions_by_user.items()]
    tuple_version = sorted(
        tuple_version,
        key=lambda x: sum(x[1].values()),
        reverse=True
    )
    for each in tuple_version:
        key = each[0]

        user_info = get_user_info(key)
        username = user_info[0]
        group = user_info[1]

        value = each[1]
        text = ""
        for action_level in key_list:
            if action_level not in value:
                text += "0".ljust(str_len)
            else:
                text += str(value[action_level]).ljust(str_len)
        table1 += (
            str(username).ljust(str_len)
            + str(key).ljust(str_len)
            + str(group).ljust(str_len)
            + text
        ) + "\n"

    table2 = ""
    # Hostname counts
    key_list = []
    for uid in hosts_by_user:
        keys = hosts_by_user[uid].keys()
        key_list.extend(keys)
    key_list = sorted(list(set(key_list)))

    # Print a text-only table for hostname counts
    header = (
        "username".ljust(str_len)
        + "uid".ljust(str_len)
        + "group".ljust(str_len)
        + "".join([k.ljust(str_len) for k in key_list])
    )
    table2 += header + "\n"
    # Sort the table by using a list of tuples
    # The tuple is formatted as (key, value) for each item in the dictionary
    # This lets us sort the table to show the most relevant items first
    tuple_version = [(key, value) for key, value in hosts_by_user.items()]
    tuple_version = sorted(
        tuple_version,
        key=lambda x: sum(x[1].values()),
        reverse=True
    )
    for each in tuple_version:
        key = each[0]

        user_info = get_user_info(key)
        username = user_info[0]
        group = user_info[1]

        value = each[1]
        text = ""
        for host in key_list:
            if host not in value:
                text += "0".ljust(str_len)
            else:
                text += str(value[host]).ljust(str_len)
        table2 += (
            str(username).ljust(str_len)
            + str(key).ljust(str_len)
            + str(group).ljust(str_len)
            + text
        ) + "\n"

    table3 = ""
    # Process counts
    header = "process".ljust(str_len) + "count".ljust(str_len)
    table3 += header + "\n"
    unsorted_list = [(key, val) for key, val in times_proc_seen.items()]
    sorted_result = sorted(unsorted_list, key=lambda x: x[1], reverse=True)
    for pair in sorted_result:
        table3 += (pair[0].ljust(str_len) + str(pair[1]).ljust(str_len)) + "\n"

    if times_new_proc_seen:
        table4 = ""
        header = "process".ljust(str_len) + "count".ljust(str_len)
        table4 += header + "\n"
        unsorted_list = [(key, val) for key, val in times_new_proc_seen.items()]
        sorted_result = sorted(unsorted_list, key=lambda x: x[1], reverse=True)
        for pair in sorted_result:
            table4 += (pair[0].ljust(str_len) + str(pair[1]).ljust(str_len)) + "\n"
        return [table1, table2, table3, table4]

    else:
        return [table1, table2, table3]


def bootstrap(args):
    """
    Configures the program so that it can function correctly. This is done by
    changing into the arbiter directory and then importing arbiter functions.
    """
    # Make the path to files absolute. This makes behavior consistent when
    # changing directories. Otherwise, configuration files would be relative to
    # the arbiter/ directory
    args.configs = [os.path.abspath(path) for path in args.configs]
    os.chdir(args.arbdir)
    insert(args.arbdir)
    insert(args.etc)

    import cfgparser
    try:
        if not cfgparser.load_config(*args.configs, check=False):
            print("There was an issue with the specified configuration (see "
                  "above). You can investigate this with the cfgparser.py "
                  "tool.")
            sys.exit(2)
    except (TypeError, toml.decoder.TomlDecodeError) as err:
        print("Configuration error:", str(err), file=sys.stderr)
        sys.exit(2)


def insert(context):
    """
    Appends a path to into the Python path.
    """
    context_path = os.path.dirname(__file__)
    sys.path.insert(0, os.path.abspath(os.path.join(context_path, context)))


def arbiter_environ():
    """
    Returns a dictionary with the ARB environment variables. If a variable is
    not found, it is not in the dictionary.
    """
    env = {}
    env_vars = {
        "ARBETC": ("-e", "--etc"),
        "ARBDIR": ("-a", "--arbdir"),
        "ARBCONFIG": ("-g", "--config")
    }
    for env_name, ignored_prefixes in env_vars.items():
        env_value = os.environ.get(env_name)
        if not env_value:
            continue
        warn = lambda i, s: print("{} in {} {}".format(i, env_name, s))
        expanded_path = lambda p: os.path.expandvars(os.path.expanduser(p))

        for prefix in ignored_prefixes:
            if env_value.startswith(prefix):
                env_value = env_value.lstrip(prefix).lstrip()
                break

        if env_name == "ARBCONFIG":
            config_paths = shlex.split(env_value, comments=False, posix=True)
            valid_paths = []
            for path in config_paths:
                if not os.path.isfile(expanded_path(path)):
                    warn(path, "does not exist")
                    continue
                valid_paths.append(path)

            if valid_paths:
                env[env_name] = valid_paths
            continue

        expanded_value = expanded_path(env_value)
        if not os.path.exists(expanded_value):
            warn(env_value, "does not exist")
            continue
        if not os.path.isdir(expanded_value):
            warn(env_value, "is not a directory")
            continue
        if (
            env_name == "ARBDIR"
            and not os.path.exists(expanded_value + "/arbiter.py")
        ):
            warn(
                env_value,
                "does not contain Arbiter2 modules! (not arbiter/?)"
            )
            continue
        if (
            env_name == "ARBETC"
            and not os.path.exists(expanded_value + "/integrations.py")
        ):
            warn(
                env_value,
                "does not contain etc modules (no integrations.py)!"
            )
            continue
        env[env_name] = expanded_value
    return env


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Arbiter statistics reporting"
    )
    arb_environ = arbiter_environ()

    # Arguments for the script
    parser.add_argument(
        "-a", "--arbdir",
        type=str,
        help="Sets the directory from which Arbiter2 modules are loaded. "
             "Defaults to $ARBDIR if present or ../arbiter otherwise.",
        default=arb_environ.get("ARBDIR", "../arbiter"),
        dest="arbdir"
    )
    parser.add_argument(
        "-g", "--config",
        type=str,
        nargs="+",
        help="The configuration files to use. Configs will be cascaded "
             "together starting at the leftmost (the primary config) going "
             "right (the overwriting configs). Defaults to $ARBCONFIG if "
             "present or ../etc/config.toml otherwise.",
        default=arb_environ.get("ARBCONFIG", ["../etc/config.toml"]),
        dest="configs"
    )
    parser.add_argument(
        "-e", "--etc",
        type=str,
        help="Sets the directory from which configurable modules are loaded "
             "(e.g. integrations.py). If a required module does not exist in "
             "the given directory, the default module will be loaded from "
             "$ARBETC if present or ../etc otherwise.",
        default=arb_environ.get("ARBETC", "../etc"),
        dest="etc"
    )
    parser.add_argument(
        "--from",
        type=str,
        default="",
        help="The sending email address. By default, the reports are sent by "
             "the email address in the configuration files.",
        dest="sender"
    )
    parser.add_argument(
        "--to",
        type=str,
        nargs="+",
        default=[],
        help="The users who will receive the report message. This should be "
             "an email address or a list of email addresses. By default, the "
             "reports are sent to the administrators' emails.",
        dest="to"
    )
    parser.add_argument(
        "--start",
        type=str,
        default="",
        help="The start date for relevant logs (e.g. 2020-01-01). Logs "
             "outside this range are ignored.",
        dest="start"
    )
    parser.add_argument(
        "--end",
        type=str,
        default="",
        help="The end date for relevant logs (e.g. 2020-01-01). Logs outside "
             "this range are ignored.",
        dest="end"
    )
    parser.add_argument(
        "--subject",
        type=str,
        default="",
        help="The email subject. If not set, a placeholder will be used with "
             "information about the date range.",
        dest="subject"
    )
    parser.add_argument(
        "--sendemail",
        action="store_true",
        help="Whether to send an email (flag). If not set, no email will be "
             "sent. This is useful for debugging.",
        dest="sendemail"
    )
    parser.add_argument(
        "--numdays",
        type=int,
        default=7,
        help="Fallback for number of days if a start day is not specified. "
             "This allows the reporting tool to operate without a defined "
             "time range by assuming the number of days to consider.",
        dest="numdays"
    )
    parser.add_argument(
        "--loglocation",
        type=str,
        default="",
        help="Location of logs. Defaults to cfg.database.log_location up to "
             "the last slash if not specified. For example, ../logs/hostname "
             "becomes ../logs/.",
        dest="loglocation"
    )
    parser.add_argument(
        "--replyto",
        type=str,
        default="",
        help="The reply-to email address. Defaults to the value set in "
             "configuration files.",
        dest="replyto"
    )
    parser.add_argument(
        "--processhistory",
        type=str,
        default="",
        help="The (optional) file in which process names are stored to provide "
             "information about newly seen processes.",
        dest="processhistory"
    )

    args = parser.parse_args()
    bootstrap(args)

    from cfgparser import cfg, shared
    import actions
    import logdb

    # Some of the information is duplicated by sending it twice (once in the
    # args variable and again as individual variables), but this makes it
    # easy to adjust parameters and apply defaults once the full environment is
    # available.
    main(
        args,
        email_from=args.sender,
        email_to=args.to,
        date_start=args.start,
        date_end=args.end,
        send_email=args.sendemail,
        email_subject=args.subject,
        date_fallback_interval=args.numdays,
        logdb_name=shared.logdb_name,
        log_location=args.loglocation,
        reply_to=args.replyto,
        process_history=args.processhistory
    )
