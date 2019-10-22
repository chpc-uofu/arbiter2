#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# A mini Arbiter2 that reports whether there is badness on the host. The exit
# code can also be used to determine this. Requires Arbiter2's statusdb to be
# set up (it is used to determine a user's status).
#
# Written by Dylan Gardner.
# Usage ./badsignal.py
import argparse
import collections
import functools
import os
import time
import sys
import toml
import cfgparser
from cfgparser import cfg, shared


def main(args):
    memsw_path = "/sys/fs/cgroup/memory/user.slice/memory.memsw.usage_in_bytes"
    found_badness = False
    # We aren't arbiter, so likely don't have same permissions
    cfg.processes.pss = cfg.processes.pss & (os.geteuid() == 0)
    collect = collector.Collector(1, args.interval, poll=2,
                                  rhel7_compat=args.rhel7_compat)
    _, users = collect.run()

    for uid, user_obj in users.items():
        cpu_usage = user_obj.cpu_usage
        cpu_quota = user_obj.cpu_quota
        mem_usage = user_obj.mem_usage
        mem_quota = user_obj.mem_quota

        whlist_cpu_usage = 0
        if args.whitelist:
            # Recall that we only whitelist cpu usage since we can throttle it,
            # unlike memory.
            whlist_cpu_usage, _ = user_obj.avg_proc_usage(whitelisted=True)
            cpu_usage -= whlist_cpu_usage

        is_bad_cpu = cpu_usage / cpu_quota > cfg.badness.cpu_badness_threshold
        is_bad_mem = mem_usage / mem_quota > cfg.badness.mem_badness_threshold
        is_bad = is_bad_cpu or is_bad_mem
        found_badness |= is_bad
        if (is_bad and not args.quiet) or args.verbose or args.debug:
            print(f"{uid:<9}\t(cpu {cpu_usage:.3f})\t(mem {mem_usage:.3f})")

        if args.debug:
            debug_info = {
                "Whitelisted Usage": (whlist_cpu_usage, 0.0),
                "Processes": set(map(str, user_obj.history[0]["pids"].values())),
                "PSS on?": cfg.processes.pss,
                "Memsw on?": cfg.processes.memsw,
                "Memsw avail?": os.path.exists(memsw_path)
            }
            for name, info in debug_info.items():
                print("  {}: {}".format(name, info))
            print()
    return 1 if found_badness else 0


def configure(args):
    """
    Configures the program so that it can function correctly.
    """
    os.chdir(args.arbdir)  # So we can load the whitelist and read statusdb
    insert(args.arbdir)    # So we can use the Arbiter2 modules
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
    Appends a path to into the python path.
    """
    context_path = os.path.dirname(__file__)
    sys.path.insert(0, os.path.abspath(os.path.join(context_path, context)))


if __name__ == "__main__":
    desc = "A mini Arbiter2 that reports whether there is badness on the " \
           "host. The exit code can also be used to determine this. " \
           "Requires Arbiter2's statusdb to be set up (it is used to " \
           "determine a user's status)."
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument("-a", "--arbdir",
                        type=str,
                        help="Sets working directory in which Arbiter2 "
                             "typically runs in. Defaults to ../arbiter.",
                        default="../arbiter",
                        dest="arbdir")
    parser.add_argument("-g", "--config",
                        type=str,
                        nargs="+",
                        default=["../etc/config.toml"],
                        help="The configuration files to use. Configs will be "
                             "cascaded together starting at the leftmost (the "
                             "primary config) going right (the overwriting "
                             "configs). Defaults to ../etc/config.toml.",
                        dest="configs")
    parser.add_argument("-i", "--interval",
                        type=int,
                        default=0.5,
                        help="The interval to average usage over.",
                        dest="interval")
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
    configure(args)

    # These have to be imported here since we need to load arbdir first
    import collector

    sys.exit(main(args))

