#!/usr/bin/env python3
import argparse
import cfgparser
import getpass
import time
import os
import pwd
import sys
import toml


def main(args):
    # Import here since the modules need to be imported based on args
    import statuses
    status_config = statuses.StatusConfig(
        status_loc=args.database_loc,
        status_table=cfgparser.shared.status_tablename
    )
    status = statuses.get_status(
        pwd.getpwnam(args.username).pw_uid,
        status_config=status_config
    )
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
    }
    print(format_properties(properties))


def format_properties(properties):
    """
    Returns user readable status string.
    """
    return "\n".join(
        [f"{name + ':':<20}{val:>10}" for name, val in properties.items()]
    )


def configure(args):
    """
    Configures the program so that it can function correctly.
    """
    try:
        config = cfgparser.combine_toml(*args.configs)
        if not cfgparser.check_config(config, pedantic=False):
            print("There was an issue with the specified configuration (see "
                  "above). You can investigate this with the cfgparser.py "
                  "tool.")
            sys.exit(2)
        else:
            cfg = cfgparser.cfg
            shared = cfgparser.shared
            cfg.add_subconfig(config)
            if not args.database_loc:
                args.database_loc = cfg.database.log_location + "/" + shared.statusdb_name
    except (TypeError, toml.decoder.TomlDecodeError) as err:
        print("Configuration error:", str(err), file=sys.stderr)
        sys.exit(2)


def append(context):
    """
    Appends a path to into the python path.
    """
    context_path = os.path.dirname(__file__)
    sys.path.insert(0, os.path.abspath(os.path.join(context_path, context)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arbiter status reporter")
    parser.add_argument("username",
                        nargs="?",
                        help="Queries the status of the user by username.",
                        default=getpass.getuser(),
                        type=str)
    parser.add_argument("-a", "--arbdir",
                        type=str,
                        help="Sets the directory in which arbiter modules "
                             "are loaded from. Defaults to ../arbiter.",
                        default="../arbiter",
                        dest="arbdir")
    parser.add_argument("-g", "--config",
                        type=str,
                        nargs="+",
                        default=["../etc/config.toml"],
                        help="The configuration files to use. Configs will be "
                             "cascaded together starting at the leftmost (the "
                             "primary config) going right (the overwriting "
                             "configs).",
                        dest="configs")
    parser.add_argument("-d", "--database",
                        type=str,
                        help="Pulls from the specified database. Defaults "
                             "to the location specified in the configuration.",
                        dest="database_loc")
    args = parser.parse_args()
    append(args.arbdir)  # Pull from the specified arbiter/ directory
    configure(args)
    main(args)
