# SPDX-FileCopyrightText: Copyright (c) 2019-2021 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only
#
# Changes a user's status to their default status group, or the given status
# group, synced across this node's sync group. This tool does not currently
# handle the case of putting someone in penalty, but can remove someone from
# penalty.
#
# Written by Jackson McKay
# Usage: ./arbupdate.py username [status_group]

import argparse
import getpass
import os
import pwd
import shlex
import sys
import time

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
    """
    Given args, change the status of user.
    """
    if args.status_group is not None and statuses.lookup_is_penalty(args.status_group):
        # Setting penalties will require special care since the occurrences
        # and timestamps internal to Status() (whatever .upgrade_penalty()
        # does) will need to be updated when a penalty is set (permitting
        # a correct lowering later on)
        exit("Cannot set penalties (not implemented)")

    statusdb_url = parse_statusdb_url(args)
    statusdb_obj = statusdb.lookup_statusdb(statusdb_url)
    uid = pwd.getpwnam(args.username).pw_uid

    # all of the current statuses in the database
    status_dict = statusdb_obj.read_raw_status()

    # our users current statuses on all active hosts
    host_statuses = status_dict[uid]

    # for each host this user is active on, change their status
    status = None
    for hostname, status in host_statuses.items():
        if args.status_group == None:
            # The user's default may be e.g. 'normal', or 'admin',
            # depending on the user and the configuration; fallback to that
            # if the user has no specific status group in mind
            status.override_status_group(status.default)

        else:
            status_groups_list = cfg.status.order + cfg.status.penalty.order
            options = ", ".join(status_groups_list)
            if args.status_group not in status_groups_list:
                exit("Invalid status '" + args.status_group + "'. Leave empty for default, or use any of: " + options)

            status.override_status_group(args.status_group)

    user_only_status_dict = {uid: host_statuses}
    statusdb_obj.write_raw_status(user_only_status_dict)
    if status is not None:
        print("user " + args.username + " status changed to " + status.current, file=sys.stderr)
    else:
        print("no change to user " + args.username + "; no statuses present", file=sys.stderr)


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
    desc = ("Changes a user's status to their default status group, or the given "
            "status group, synced across this node's sync group (requires that an"
            "Arbiter2 instance has previously ran on this host). This tool does "
            "not currently handle the case of putting someone in penalty, but "
            "can remove someone from penalty.")
    parser = argparse.ArgumentParser(description=desc)
    arb_environ = arbiter_environ()
    parser.add_argument("username",
                        help="The user to target the status group change against.",
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
    parser.add_argument("status_group",
                        type=str,
                        nargs="?",
                        help="The new status group to put a user in. Statuses "
                             "are defined by the configuration files. "
                             "Defaults to the default status group of a user "
                             "defined in the [statuses] section of the "
                             "configuration.",
                        default=None)
    env = parser.add_mutually_exclusive_group()
    env.add_argument("-u", "--statusdb-url",
                     type=str,
                     help="Uses the specified statusdb url. Defaults "
                          "to database.statusdb_url specified in the "
                           "configuration.",
                    dest="statusdb_url")
    env.add_argument("-d", "--database",
                     type=str,
                     help="Uses the specified sqlite statusdb, rather "
                          "than database.statusdb_url specified in the "
                          "configuration. In this case, no synchronization "
                          "to other hosts will occur, since local SQLite "
                          "databases are not shared between hosts.",
                     dest="database_loc")
    args = parser.parse_args()
    bootstrap(args)
    import statusdb
    from cfgparser import cfg
    main(args)
