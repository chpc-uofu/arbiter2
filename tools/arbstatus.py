#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only
#
# Written by Dylan Gardner
# Usage: ./arbstatus.py

import argparse
import shlex
import getpass
import time
import os
import pwd
import sys
import toml


def parse_statusdb_url(args):
    """
    Given args, return the correct statusdb url.
    """
    # Local sqlite database
    if args.database_loc:
        return "sqlite:///{}".format(args.database_loc)
    # Provided URL
    elif args.statusdb_url:
        return args.statusdb_url
    # Resolve to what statusdb_url is when empty in the config
    elif cfg.database.statusdb_url == "":
        return "sqlite:///{}".format(cfg.database.log_location + "/statuses.db")
    # Use statusdb_url from the config
    else:
        return cfg.database.statusdb_url


def main(args):
    statusdb_url = parse_statusdb_url(args)
    statusdb_obj = statusdb.lookup_statusdb(statusdb_url)

    status = statusdb_obj.get_status(pwd.getpwnam(args.username).pw_uid)
    timeout = float("inf")
    if statuses.lookup_is_penalty(status.current):
        timeout = statuses.lookup_status_prop(status.current).timeout

    timeleft = timeout - (time.time() - status.timestamp)
    # Cannot cast inf to int
    timeleft = int(timeleft) if timeleft != float("inf") else timeleft
    properties = {
        "Status": status.current,
        "Time Left": f"{timeleft}{'s' if timeleft != float('inf') else ''}",
        "Penalty Occurrences": status.occurrences,
        "Default Status": status.default,
        "Authority": status.authority,
    }
    print("\n".join(
        [f"{name + ':':<20}{val:>10}" for name, val in properties.items()]
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
    parser = argparse.ArgumentParser(description="Arbiter status reporter")
    arb_environ = arbiter_environ()
    parser.add_argument("username",
                        nargs="?",
                        help="Queries the status of the user by username.",
                        default=getpass.getuser(),
                        type=str)
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
    env = parser.add_mutually_exclusive_group()
    env.add_argument("-u", "--statusdb-url",
                     type=str,
                     help="Pulls from the specified statusdb url. Defaults "
                          "to database.statusdb_url specified in the "
                           "configuration.",
                    dest="statusdb_url")
    env.add_argument("-d", "--database",
                     type=str,
                     help="Pulls from the specified sqlite statusdb, rather "
                          "than database.statusdb_url specified in the "
                          "configuration.",
                     dest="database_loc")
    args = parser.parse_args()
    bootstrap(args)
    import statusdb
    import statuses
    import database
    from cfgparser import cfg
    main(args)
