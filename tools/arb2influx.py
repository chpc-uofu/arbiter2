#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# A program that reads status databases and sends the associated information,
# such as statuses and badness scores, to an InfluxDB instance. This is largely
# a proof-of-concept for integrating Arbiter2 with monitoring infrastructure.
#
# Written by Robben Migacz
# Usage: ./arb2influx.py (to see available arguments)
import argparse
import shlex
import pwd
import grp
import os
import sys
import toml
import time
import influxdb

# These parameters likely need to be adjusted
# FIXME: Add as arguments to the script!
host = ""
port = 8086
username = ""
password = ""
db = ""


def get_user_info(uid):
    uid = int(uid)  # Ensure an integer is used as the uid (or it won't work)
    # Try to get the username and group ID
    try:
        pwd_info = pwd.getpwuid(uid)
        username = pwd_info.pw_name
        groupid = pwd_info.pw_gid
        group = grp.getgrgid(groupid).gr_name
    except:
        return None
    return (str(username), str(group))


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


def main(args):
    """Reads status databases and formats as JSON
    """
    json_body = []
    files_to_read = []
    # FIXME? This assumes the hostname is the last field in the path!
    # With other log hierarchies, this probably won't work.
    log_location = "/".join(cfg.database.log_location.split("/")[:-2])

    # Walk the log location and get filenames
    walked = os.walk(log_location)
    for (directory, subdirectories, files) in walked:
        files_to_read.extend(
            [[directory, f]  # Keep hostname, filename
             for f in files
             if f == shared.statusdb_name]  # Keep only the relevant files
        )

    for pair in files_to_read:
        directory = os.path.join(pair[0], pair[1])
        # A bit of an ugly hack to get the hostname (FIXME?)
        hostname = directory.replace(shared.statusdb_name, "").replace(log_location, "").replace("/", "")
        filename = pair[1]

        status_config = statuses.StatusConfig(
            status_loc=directory,
            status_table=shared.status_tablename
        )
        user_statuses = statuses.read_status(status_config=status_config)
        user_badness_vals = statuses.read_badness(status_config=status_config)

        # Collect information about each user
        uids = []
        for uid in user_statuses.keys():
            uids.append(uid)
        for uid in user_badness_vals.keys():
            uids.append(uid)
        user_info_map = {}
        for uid in set(uids):
            user_info = get_user_info(uid)
            if user_info:
                user_info_map[uid] = user_info

        current_time = int(time.time())

        for uid, status in user_statuses.items():
            if not uid in user_info_map:
                continue
            # Skip old points, which will clutter plots
            if current_time - status.timestamp > cfg.general.arbiter_refresh * 2:
                continue
            json = {
                "measurement": "arbiter_status",
                "tags": {
                    "user": uid,
                    "username": user_info_map[uid][0],
                    "group": user_info_map[uid][1],
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

        for uid, badness in user_badness_vals.items():
            if not uid in user_info_map:
                continue
            # Skip old points, which will clutter plots
            if current_time - badness["timestamp"] > cfg.general.arbiter_refresh * 2:
                continue
            json = {
                "measurement": "arbiter_badness",
                "tags": {
                    "user": uid,
                    "username": user_info_map[uid][0],
                    "group": user_info_map[uid][1],
                    "hostname": hostname
                },
                "fields": {
                    "cpu": badness["cpu"],
                    "mem": badness["mem"],
                    "timestamp": badness["timestamp"]
                }
            }
            json_body.append(json)
    to_influx(json_body)


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
        if not args.database_loc:
            cfg, shared = cfgparser.cfg, cfgparser.shared
            args.database_loc = cfg.database.log_location + "/" + shared.statusdb_name
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
    parser = argparse.ArgumentParser(description="Arbiter to InfluxDB")
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
    parser.add_argument("-d", "--database",
                        type=str,
                        help="Pulls from the specified database. Defaults "
                             "to the location specified in the configuration.",
                        dest="database_loc")
    args = parser.parse_args()
    bootstrap(args)
    import statuses
    from cfgparser import cfg, shared
    main(args)
