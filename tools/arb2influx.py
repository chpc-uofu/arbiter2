#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only
#
# A program that reads status databases and sends the associated information,
# such as statuses and badness scores, to an InfluxDB instance. This is largely
# a proof-of-concept for integrating Arbiter2 with monitoring infrastructure.
#
# Written by Robben Migacz
# Usage: ./arb2influx.py (to see available arguments)
import argparse
import functools
import grp
import os
import pwd
import shlex
import sys
import time
import toml
import urllib.parse
import netrc
import influxdb

# These parameters likely need to be adjusted
# FIXME: Add as arguments to the script!
host = ""
port = 8086
username = ""
password = ""
db = ""


# Cache the results so we don't have to do two lookups for statuses and badness
# 4096 penalties is extremely unlikely but no harm with large values here
@functools.lru_cache(maxsize=4096)
def get_user_info(uid):
    """
    Returns a username, groupname pair for the given uid or None if the user
    doesn't have a passwd entry.
    """
    uid = int(uid)  # Ensure an integer is used as the uid (or it won't work)
    # Try to get the username and group ID
    try:
        pwd_info = pwd.getpwuid(uid)
        username = pwd_info.pw_name
        groupid = pwd_info.pw_gid
        group = grp.getgrgid(groupid).gr_name
    except:
        return None
    return username, group


def to_influx(
    json_body,
    host=host,
    port=port,
    username=username,
    password=password,
    db=db
):
    """Writes JSON data to an InfluxDB instance
    """
    try:
        client = influxdb.InfluxDBClient(host, port, username, password, db)
        client.write_points(json_body)
    except influxdb.exceptions.InfluxDBClientError as err:
        print("An error occurred in the InfluxDB request: %s" % err)
        sys.exit(1)
    except influxdb.exceptions.InfluxDBServerError as err:
        print("InfluxDB had a server error: %s" % err)
        sys.exit(1)


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
    """Reads status databases and formats as JSON
    """
    json_body = []
    statusdb_url = parse_statusdb_url(args)
    statusdb_obj = statusdb.lookup_statusdb(statusdb_url)

    badness_timeout = cfg.general.arbiter_refresh * 2
    try:
        user_hosts_status = statusdb_obj.read_raw_status()
    except statusdb.common_db_errors as err:
        print("Failed to access statusdb:", err)
        sys.exit(1)

    for uid, hosts_status in user_hosts_status.items():
        user_info = get_user_info(uid)
        # Skip users with no passwd entry (see collector.refresh_uids)
        if not user_info:
            continue

        username, group = user_info
        for hostname, status in hosts_status.items():
            json = {
                "measurement": "arbiter_status",
                "tags": {
                    "user": uid,
                    "username": username,
                    "group": group,
                    "hostname": hostname
                },
                "fields": {
                    "current": status.current,
                    "default": status.default,
                    "occurrences": status.occurrences,
                    "timestamp": status.timestamp,
                    "occur_timestamp": status.occur_timestamp
                }
            }
            json_body.append(json)

    try:
        user_badness = statusdb_obj.read_badness()
    except statusdb.common_db_errors as err:
        print("Failed to access statusdb:", err)
        sys.exit(1)

    for uid, badness_obj in user_badness.items():
        user_info = get_user_info(uid)
        # Skip users with no passwd entry (see collector.refresh_uids)
        if not user_info:
            continue

        username, group = user_info
        # Skip old points, which will clutter plots
        if badness_obj.expired(timeout=badness_timeout):
            continue

        json = {
            "measurement": "arbiter_badness",
            "tags": {
                "user": uid,
                "username": username,
                "group": group,
                "hostname": hostname
            },
            "fields": {
                "cpu": badness_obj.cpu,
                "mem": badness_obj.mem,
                "timestamp": badness_obj.last_updated()
            }
        }
        json_body.append(json)

    to_influx(json_body, username, password, host, port, db)


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
        if env_name == "ARBDIR" and not os.path.exists(expanded_value + "/arbiter.py"):
            warn(env_value, "does not contain arbiter modules! (not arbiter/ ?)")
            continue
        if env_name == "ARBETC" and not os.path.exists(expanded_value + "/integrations.py"):
            warn(env_value, "does not contain etc modules! (no integrations.py)")
            continue
        env[env_name] = expanded_value
    return env


if __name__ == "__main__":
    desc = "Exports arbiter measurements to given InfluxDB instance. "
    parser = argparse.ArgumentParser(description=desc)
    arb_environ = arbiter_environ()
    parser.add_argument("-a", "--arbdir",
                        type=str,
                        help="Sets the directory in which Arbiter modules "
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
    parser.add_argument("--netrc",
                        type=str,
                        default=os.path.expanduser("~") + "/.netrc",
                        help="Pulls InfluxDB authentication from the user's "
                             ".netrc file or the specified one.",
                        dest="netrc")
    args = parser.parse_args()
    bootstrap(args)
    import statusdb
    import database
    from cfgparser import cfg, shared
    main(args)
