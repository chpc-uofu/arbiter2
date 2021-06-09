#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
# SPDX-FileCopyrightText: Copyright (c) 2020 Idaho National Laboratory
#
# SPDX-License-Identifier: GPL-2.0-only
#
# A mini Arbiter2 that reports whether there is badness on the host. The exit
# code can also be used to determine this. Requires Arbiter2's statusdb to be
# set up (it is used to determine a user's status).
#
# Written by Dylan Gardner
# Usage ./badsignal.py
import argparse
import collections
import functools
import os
import pwd
import shlex
import sys
import time
import toml


def main(args):
    memsw_path = "/sys/fs/cgroup/memory/user.slice/memory.memsw.usage_in_bytes"
    found_badness = False
    # We aren't arbiter, so likely don't have same permissions
    cfg.processes.pss = cfg.processes.pss & (os.geteuid() == 0)

    arbiter_refresh = cfg.general.arbiter_refresh
    history_per_refresh = cfg.general.history_per_refresh
    poll = cfg.general.poll

    repetitions = args.repetitions if args.repetitions else history_per_refresh
    interval = args.interval if args.interval else arbiter_refresh / history_per_refresh
    poll = args.poll if args.poll else poll

    collector_obj = collector.Collector(
        repetitions,
        interval,
        poll=poll,
        rhel7_compat=args.rhel7_compat
    )
    _, users = collector_obj.run()

    for uid, user_obj in users.items():
        try:
            username = pwd.getpwuid(uid).pw_name
            uid_name = f"{uid} ({username})"
        except KeyError:
            uid_name = str(uid)

        cpu_usage, mem_usage = user_obj.last_cgroup_usage()
        cpu_quota, mem_quota = user_obj.status.quotas()

        whlist_cpu_usage = 0
        if args.whitelist:
            # Recall that we only whitelist cpu usage since we can throttle it,
            # unlike memory.
            whlist_cpu_usage, _ = user_obj.last_proc_usage(whitelisted=True)
            cpu_usage -= whlist_cpu_usage

        is_bad_cpu = cpu_usage / cpu_quota > cfg.badness.cpu_badness_threshold
        is_bad_mem = mem_usage / mem_quota > cfg.badness.mem_badness_threshold
        is_bad = is_bad_cpu or is_bad_mem
        found_badness |= is_bad
        if (is_bad and not args.quiet) or args.verbose or args.debug:
            # 25: max 9 digit uid + 16 username
            # Note: Max username length is 32 chars (man 8 useradd) on linux,
            #       but ps and similar utilities don't display past 8 chars.
            #       We'll use 16 chars because we're nice but not 32 chars
            #       nice because that's a waste of space for annoying to type
            #       usernames :^)
            print(f"{uid_name:<25}\t(cpu {cpu_usage:.3f})\t(mem {mem_usage:.3f})")

        if args.debug:
            debug_info = {
                "Whitelisted Usage": (whlist_cpu_usage, 0.0),
                "Process Usage": user_obj.avg_proc_usage(whitelisted=False)
            }
            for event in range(repetitions)[::-1]:  # .history goes backwards
                proc_list = list(user_obj.history[event]["pids"].values())
                debug_info["Process Event {}".format(event)] = [
                    proc.debug_str()
                    for proc in usage.rel_sorted(
                        proc_list,
                        cpu_quota, mem_quota,
                        key=lambda p: (p.usage["cpu"], p.usage["mem"]),
                        reverse=True
                    )
                ]

            debug_info.update({
                "PSS on?": cfg.processes.pss,
                "Memsw on?": cfg.processes.memsw,
                "Memsw avail?": os.path.exists(memsw_path)
            })
            for name, info in debug_info.items():
                print("  {}: {}".format(name, info))
            print()
    return 1 if found_badness else 0


def bootstrap(args):
    """
    Configures the program so that it can function correctly.
    """
    # Make the path to files absolute. This makes behavior consistent when
    # changing directories. Otherwise, configuration files would be relative to
    # the arbiter/ directory
    args.configs = [os.path.abspath(path) for path in args.configs]
    os.chdir(args.arbdir)
    insert(args.arbdir)
    import cfgparser
    try:
        if not cfgparser.load_config(*args.configs, check=False, pedantic=False):
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
    desc = "A mini Arbiter2 that reports whether there is badness on the " \
           "host. The exit code can also be used to determine this. " \
           "Requires Arbiter2's statusdb to be set up (it is used to " \
           "determine a user's status)."
    parser = argparse.ArgumentParser(description=desc)
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
    parser.add_argument("-i", "--interval",
                        type=int,
                        help="The interval to average usage over. Defaults "
                             "to cfg.general.arbiter_refresh // "
                             "cfg.general.history_per_refresh.",
                        dest="interval")
    parser.add_argument("-r", "--repetitions",
                        type=int,
                        help="How many collector repetitions to do. Defaults "
                             "to cfg.general.history_per_refresh.",
                        dest="repetitions")
    parser.add_argument("-p", "--poll",
                        type=int,
                        help="How many collector polls per repetition to do. "
                             "Defaults to cfg.general.poll.",
                        dest="poll")
    parser.add_argument("--rhel7-compat",
                        action="store_true",
                        help="Run with a special configuration that allows "
                             "for compatability with rhel7/centos7 (Kernel "
                             "3.10). Among other things, this configuration "
                             "replaces memory acct from cgroups with pid "
                             "memory data.",
                        dest="rhel7_compat")
    parser.add_argument("-w", "--no-whitelist",
                        action="store_false",
                        help="Allows whitelisted processes to be counted "
                             "towards badness.",
                        dest="whitelist")
    env = parser.add_mutually_exclusive_group()
    env.add_argument("-d", "--debug",
                     action="store_true",
                     help="Print debugging information to help identify "
                          "issues with Arbiter2 and its config.",
                     dest="debug")
    env.add_argument("-q", "--quiet",
                     action="store_true",
                     help="Silences the script.",
                     dest="quiet")
    env.add_argument("-v", "--verbose",
                     action="store_true",
                     help="Prints out all the users usage information.",
                     dest="verbose")
    args = parser.parse_args()
    bootstrap(args)
    import usage
    import collector
    from cfgparser import cfg
    sys.exit(main(args))
