#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only
#
# Cleans up the given status databases (before 1.4.0, Arbiter2 did not
# properly maintain and cleanup it's statusdb).
#
# Written by Dylan Gardner.
# Usage ./cleanup-statuses.py
import argparse
import shlex
import os
import sys
import time
import pwd
import toml


def main(args):
    if not args.database_locs:
        args.database_locs = [cfg.database.log_location + "/statuses.db"]

    for db in args.database_locs:
        statusdb_url = "sqlite:///" + db
        statusdb_obj = statusdb.lookup_statusdb(statusdb_url)

        refresh_margin = cfg.general.arbiter_refresh * 5

        # Go through each status and potentially remove that user
        try:
            user_statuses  = statusdb_obj.read_status()
        except statusdb.common_db_errors as err:
            print("Failed to access statusdb {}: {}".format(db, err))
            continue

        for uid, status in user_statuses.items():
            uid = int(uid)

            # Sometimes there are users with records in statusdb that aren't
            # in ldap (or whatever the equivelant is) because they've been
            # removed up there at some point (i.e. have no passwd entry), but
            # obviously had been on a machine with a passwd at some point.
            # This causes problems if you make the assumption that every user
            # you see has a passwd entry, so we'll remove the record of them
            # here. Arbiter2 ignores these users and can deal with this, but
            # it's unnecessary cruft.
            try:
                username = pwd.getpwuid(uid).pw_name
            except KeyError:
                print("Removing {} (?) in statuses because they don't have a "
                      "passwd entry".format(uid))
                statusdb_obj._remove_user(uid)
                continue

            # This really shouldn't happen since the non-cleanup bug before
            # 1.4.0 properly removed penalty statuses from statusdb, but it's
            # a valid future possibility
            if args.penalty_timeouts and statuses.lookup_is_penalty(status.current):
                timeout = statuses.lookup_status_prop(status.current).timeout
                timeout += refresh_margin
                if time.time() - int(status.timestamp) >= timeout:
                    print("Removing {} ({}) in statuses because their "
                          "penalty has timed out ({}s)".format(uid, username,
                          (time.time() - int(status.timestamp) - timeout)))
                    statusdb_obj._remove_user(uid)
                    continue

            # This was a major bug with Arbiter2 before 1.4.0 where it'd
            # occasionally untrack a user in cases where their occurrences
            # (the severity record of their previous penalty) was non-zero
            # (which we don't want), leaving them with a entry in statusdb
            # that would never get removed unless they stayed logged in until
            # their occurrences timeout for each occurrence happened (which
            # is not common).
            if args.occur_timeouts and status.occurrences > 0:
                timeout = cfg.status.penalty.occur_timeout
                timeout += refresh_margin
                if time.time() - int(status.occur_timestamp) >= timeout:
                    print("Removing {} ({}) in statuses because their "
                          "occurrences has timed out ({}s)".format(uid,
                          username, (time.time() - int(status.occur_timestamp)
                          - timeout)))
                    statusdb_obj._remove_user(uid)
                    continue

        try:
            user_badness = statusdb_obj.read_badness()
        except statusdb.common_db_errors as err:
            print("Failed to access statusdb {}: {}".format(db, err))
            continue

        for uid, badness_obj in user_badness.items():
            uid = int(uid)
            try:
                # See above (statuses section) for an explaination
                username = pwd.getpwuid(uid).pw_name
            except KeyError:
                print("Removing {} (?) in badness because they don't have a "
                      "passwd entry".format(uid))
                statusdb_obj._remove_user(uid)
                continue

            if badness_obj.is_good():
                print("Removing {} ({}) in badness because their badness is"
                      "zero".format(uid, username))
                statusdb_obj.remove_badness(uid)
                continue

            # This was a major bug with Arbiter2 before 1.4.0 where it'd
            # never remove a user's badness from statusdb (in fact it didn't
            # even have the functionality to do so!). This just causes extra
            # cruft to be left in the database, slowing down queries slightly.
            if (args.badness_timeouts and
                    (time.time() - last_updated >= refresh_margin)):
                print("Removing {} ({}) in badness because their badness has "
                      "timed out ({}s)".format(uid, username, (time.time() -
                      last_updated - refresh_margin)))
                statusdb_obj.remove_badness(uid)


def bootstrap(args):
    """
    Configures the program so that it can function correctly. This is done by
    changing into the arbiter directory and then importing arbiter functions.
    """
    # Make the path to files absolute. This makes behavior consistent when
    # changing directories. Otherwise, configuration files would be relative to
    # the arbiter/ directory
    args.configs = [os.path.abspath(path) for path in args.configs]
    if args.database_locs:
        args.database_locs = [os.path.abspath(path) for path in args.database_locs]
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
        env[env_name] = expanded_value
    return env


if __name__ == "__main__":
    desc = ("Cleans up the given status sqlite databases (before 1.4.0, "
            "Arbiter2 did not properly maintain and cleanup it's own "
            "statusdb...). Only necessary when upgrading to 1.4.0.")
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
    parser.add_argument("-d", "--database",
                        type=str,
                        nargs="*",
                        help="Pulls from the specified sqlite database(s). "
                             "Defaults to the database in the location "
                             "specified in the configuration.",
                        dest="database_locs")
    parser.add_argument("--ignore-penalty-timeouts",
                        action="store_true",
                        help="If given, ignores users from the database who "
                             "have had their penalty timeouts already, with "
                             "a 5m margin of the configured arbiter_refresh.",
                        dest="penalty_timeouts")
    parser.add_argument("--ignore-occur-timeouts",
                        action="store_false",
                        help="If given, ignores users from the database who "
                             "have had their occurrences timeouts already, "
                             "with a 5x margin of the configured "
                             "arbiter_refresh.",
                        dest="occur_timeouts")
    parser.add_argument("--ignore-badness-timeouts",
                        action="store_false",
                        help="If given, ignores users from the database who "
                             "have old badness scores with a 5m margin of the"
                             "configured arbiter_refresh.",
                        dest="badness_timeouts")
    args = parser.parse_args()
    bootstrap(args)
    import statusdb
    import statuses
    from cfgparser import shared, cfg
    main(args)
