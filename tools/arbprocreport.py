#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only
#
# Reports on processes logged in logdb that could possibly be whitelisted.
#
# Based on arbreport.py by Robben Migacz, written by Dylan Gardner
# Usage: ./arbprocreport.py

import argparse
import collections
import datetime
import multiprocessing
import os
import pwd
import shlex
import sys
import time
import toml


def parse_date(string):
    """
    Given a string containing a %Y-%m-%d formatted date, return a datetime
    object.
    """
    return datetime.datetime.strptime(string, "%Y-%m-%d")


def get_date_range_from_args(args):
    """
    Returns a start and an end datetimes from the given arguments.

    args: argparse.Namespace
        Arguments containing possibly 'start', 'end' and 'numdays'.
    """
    now = datetime.datetime.now()
    date_end = parse_date(args.end) if args.end else now
    if args.start:
        date_start = parse_date(args.start)
    else:
        date_start = now - datetime.timedelta(days=args.numdays)

    return date_start, date_end


def get_logdb_paths(args):
    """
    Returns a list of logdb paths to read.

    args: argparse.Namespace
        Arguments containing possibly 'start', 'end', 'numdays', and
        'loglocation'.
    """
    # Deal with times
    date_start, date_end = get_date_range_from_args(args)

    # Get the relevant dates
    # Cannot trust logs to be consistently spaced due to a bug in early Arbiter
    # versions, so try looking at every day
    between_datetimes = [date_start + datetime.timedelta(days=ndays)
                         for ndays in range((date_end - date_start).days + 1)]
    # Convert dates to ISO-formatted strings
    between_dates = [day.strftime("%Y-%m-%d") for day in between_datetimes]
    # Use the logdb name specification to get relevant filenames
    filenames = [shared.logdb_name.format(date) for date in between_dates]

    # Crawl the logdb directory to get relevant files
    log_location = args.loglocation
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
    for directory, _, files in walked:
        files_to_read.extend(
            [
                os.path.join(directory, f)
                for f in files
                # Keep only the relevant files (in date range)
                if f in filenames
            ]
        )
    return files_to_read


def extract_hostname_from_path(filepath):
    """
    Given a filepath, returns the corresponding hostname belonging to that
    path. e.g. '../logs/hostname/logdb.db' -> 'hostname'

    filepath: str
        A filepath to process with a hostname embeded in the path.
    """
    # FIXME: Assumes the hostname is the second-to-last hierarchy level
    #        for files, as in the default configuration. If that's not the
    #        case for you, this is probably the part you want to change.
    dirpath = os.path.dirname(filepath)  # /logs/hostname/logdb.db -> /logs/hostname
    return os.path.basename(dirpath)     # /logs/hostname -> hostname


def keyword_from_action(uid, hostname, timestamp, penalty):
    """
    Given some penalty action properites, returns a keyword to search for in
    emails.
    """
    try:
        username = pwd.getpwuid(uid).pw_name
    except KeyError:
        # The user has been deleted since the violation, we don't have the
        # username anywhere, so hopefully the day, hostname and penalty level
        # can still track it down...
        username = ""

    parts = [
        # identify email(s)
        username,
        # filter by hostname
        hostname,
        # and day
        time.strftime("%m/%d", time.localtime(timestamp)),
        # finally by penalty level
        penalty,
    ]
    return " ".join(parts)


def process_filepath(filepath, badlist):
    """
    Processes a single logdb instance.
    """
    sys.stderr.write("Processing " + filepath + "...\n")
    hostname = extract_hostname_from_path(filepath)
    logdb_obj = logdb.LogDB(filepath)
    actions = logdb_obj.read_actions()

    # Map process names to the context they were found in; Use set to
    # eliminate possibly duplicate contexts
    procnames_keyword_set = collections.defaultdict(set)
    for action_obj in actions:
        # I'd conjecture that for the most part, the processes we care
        # about are only really going to be found in the first penalty,
        # since the later penalties are usually a result of the user not
        # stopping the processes found in penalty1
        if action_obj.action != "penalty1":
            continue

        for process in action_obj.process:
            if process.name.endswith("*") or process.name in badlist:
                continue
            if process.cpu < 75:
                continue

            keyword = keyword_from_action(
                action_obj.user,
                hostname,
                action_obj.timestamp,
                action_obj.action
            )
            procnames_keyword_set[process.name].add(keyword)

    return procnames_keyword_set


def main(args):
    """
    Prints a list of processes that could be whitelisted.
    """
    badlist = set()
    if args.badlist:
        with open(args.badlist) as bf:
            badlist = set(map(str.strip, bf.readlines()))

    # Get a list of logs to process with their corresponding hostnames
    filepaths_to_read = get_logdb_paths(args)

    final_proc_kw_set = collections.defaultdict(set)
    with multiprocessing.Pool(args.parallel) as pool:
        args = [(filepath, badlist) for filepath in filepaths_to_read]
        # chunksize is the chunk size of each process on an iteration; 1 is
        # super slow due processes keep to having to asking for more; we
        # choose 4 since all processes syncronize once they eat their chunk
        # and there are some particular files which take a while to process,
        # leading to starvation if our chunksize is large...
        for proc_kw_set in pool.starmap(process_filepath, args, chunksize=4):
            for procname, kw_set in proc_kw_set.items():
                final_proc_kw_set[procname] |= kw_set

    # FIXME: Replace with f-strings
    colwidth = 18  # Linux enforces 16 bytes for proc names internally
    print("PROCESS{}NVIOLATIONS{}KEYWORD".format(
        " " * (colwidth - len("PROCESS")),
        " " * (colwidth - len("NVIOLATIONS"))
    ))
    for procname, keyword_set in final_proc_kw_set.items():
        print(("{}{}{}{}{}").format(
            procname,
            " " * (colwidth - len(procname)),
            len(keyword_set),
            " " * (colwidth - len(str(len(keyword_set)))),
            ", ".join(keyword_set),
        ))


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
    import cfgparser
    try:
        if not cfgparser.load_config(*args.configs, pedantic=False):
            print("There was an issue with the specified configuration (see "
                  "above). You can investigate this with the cfgparser.py "
                  "tool.")
            sys.exit(2)
    except (TypeError, toml.decoder.TomlDecodeError) as err:
        print("Configuration error:", str(err), file=sys.stderr)
        sys.exit(2)


def insert(context):
    """
    Appends a path to into the python path.
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
        if env_name == "ARBDIR" and not os.path.exists(expanded_value + "/arbiter.py"):
            warn(env_value, "does not contain arbiter modules! (not arbiter/ ?)")
            continue
        if env_name == "ARBETC" and not os.path.exists(expanded_value + "/integrations.py"):
            warn(env_value, "does not contain etc modules! (no integrations.py)")
            continue
        env[env_name] = expanded_value
    return env


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arbiter process statistics reporting")
    arb_environ = arbiter_environ()
    parser.add_argument("-a", "--arbdir",
                        type=str,
                        help="Sets the directory in which arbiter modules "
                             "are loaded from. Defaults to $ARBDIR if "
                             "present or ../arbiter otherwise.",
                        default=arb_environ.get("ARBDIR", "../arbiter"),
                        dest="arbdir")
    parser.add_argument("-g", "--config",
                        type=str,
                        nargs="+",
                        help="The configuration files to use. Configs will be "
                             "cascaded together starting at the leftmost (the "
                             "primary config) going right (the overwriting "
                             "configs). Defaults to $ARBCONFIG if present or "
                             "../etc/config.toml otherwise.",
                        default=arb_environ.get("ARBCONFIG", ["../etc/config.toml"]),
                        dest="configs")
    parser.add_argument("-l", "--loglocation",
                        type=str,
                        default="",
                        help="Location of logs. Defaults to "
                             "cfg.database.log_location up to the last slash "
                             "if not specified. For example, ../logs/hostname "
                             "becomes ../logs/.")
    parser.add_argument("--badlist",
                        type=str,
                        default="",
                        help="A file containing a list of non-whitelisted "
                             "processes to ignore. This helps speed up the "
                             "report generation and reduces the size of the "
                             "report.")

    dtparser = parser.add_argument_group("date ranges")
    dtparser.add_argument("--start",
                          type=str,
                          default="",
                          help="The earliest date (inclusive) to start looking "
                               "at logdb instances. Use YYYY-mm-dd format.")
    dtparser.add_argument("--end",
                          type=str,
                          default="",
                          help="The latest date (inclusive) to stop looking at "
                               "logdb instances. Use YYYY-mm-dd format.")
    dtparser.add_argument("--numdays",
                         type=int,
                         default=7,
                         help="Fallback for number of days if a start day is "
                              "not specified. Defaults to 7.")

    plparser = parser.add_argument_group("parallelism")
    plparser.add_argument("-p", "--parallel",
                          type=int,
                          default=1,
                          help="Use the given number of processes in parallel. "
                               "Defaults to 1.")
    args = parser.parse_args()

    # We chdir in bootstrap, make path abs to make things consistent
    if args.badlist:
        args.badlist = os.path.abspath(args.badlist)

    bootstrap(args)

    import database
    import logdb
    from cfgparser import cfg, shared
    main(args)
