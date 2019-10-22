#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only

"""
A starting wrapper around main.py that gets arguments and sets the etc
modules context.
"""

import argparse
import logging
import sys
import os
import logger
import cinfo
import cfgparser
import permissions
import toml

startup_logger = logging.getLogger("arbiter_startup")
service_logger = logging.getLogger("arbiter_service")
debug_logger = logging.getLogger("arbiter")


def arguments():
    """
    Defines the arguments that Arbiter2 takes in and returns them.
    """
    desc = ("Version 2 of Arbiter; uses cgroups for monitoring and managing "
            "behavior.")
    parser = argparse.ArgumentParser(description=desc)
    parser.add_argument("-a", "--accounting",
                        type=int,
                        nargs="?",
                        const=str(os.getuid()),
                        help="Turn on cgroup cpu and memory accounting for "
                             "users on startup and after a period when there "
                             "are no users. This is done by turning on "
                             "accounting for a specific inactive user, which "
                             "implicitly turns it on for other users. The "
                             "user must not log in, since logging out "
                             "destroys its permanent slice. See the install "
                             "guide for more details. Defaults to the uid of "
                             "user who runs the program.",
                        dest="acct_uid")
    parser.add_argument("-e", "--etc",
                        type=str,
                        help="Set the directory in which configurable modules "
                             "are loaded from (e.g. integrations.py). If a "
                             "required module does not exist in the new "
                             "directory, the default module will be loaded "
                             "from ../etc",
                        dest="etc")
    parser.add_argument("--exit-file",
                        metavar="FILE",
                        type=str,
                        help="If the specified file is updated in any way, "
                             "as well as being owned by the group name "
                             "specified in the configuration, Arbiter will "
                             "exit as a failure. This allows for a hacky way "
                             "to restart Arbiter with changes using systemd.",
                        dest="exit_file")
    parser.add_argument("--rhel7-compat",
                        action="store_true",
                        help="Run arbiter with a special configuration that "
                             "allows for compatability with rhel7/centos7 "
                             "(Kernel 3.10). Among other things, this "
                             "configuration replaces memory acct from cgroups "
                             "with pid memory data. See the install guide "
                             "for more information.",
                        dest="rhel7_compat")
    parser.add_argument("-s", "--sudo",
                        action="store_true",
                        help="Use sudoers permissions to write out limits and "
                             "to enable cgroup accounting. See the install "
                             "guide for more information on how to set this "
                             "up.",
                        dest="sudo_permissions")
    parser.add_argument("-p", "--print",
                        action="store_true",
                        help="Print out the application logging to stdout. By "
                             "default, logging is automatically logged out to "
                             "a file specified in the config, regardless of "
                             "this flag.",
                        dest="print_logs")
    env = parser.add_mutually_exclusive_group()
    env.add_argument("-q", "--quiet",
                     action="store_true",
                     help="Only outputs critical information to stdout if the "
                          "-p flag is used. Startup logging ignores this.",
                     dest="quiet")
    env.add_argument("-v", "--verbose",
                     action="store_true",
                     help="Turns on debugging output to stdout if the -p flag "
                          "is used.",
                     dest="verbose")
    parser.add_argument("-g", "--config",
                        type=str,
                        nargs="+",
                        default=["../etc/config.toml"],
                        help="The configuration files to use. Configs will be "
                             "cascaded together starting at the leftmost (the "
                             "primary config) going right (the overwriting "
                             "configs).",
                        dest="configs")
    args = parser.parse_args()

    # insert the default etc/ path, used as a fallback
    insert("../etc")
    if args.etc:
        # insert the new etc/ path
        insert(args.etc)
    return args


def insert(context):
    """
    Inserts a path to into the python path.

    context: str
        The path to insert into the python path.
    """
    context_path = os.path.dirname(__file__)
    sys.path.insert(0, os.path.abspath(os.path.join(context_path, context)))


def setup_config(config_files):
    """
    Loads the given configs. If there is a problem, exits with a error code of
    2.

    config_files: iter
        A iterable of configuration files to load.
    """
    startup_logger.info("Importing and validating configuration...")
    try:
        if not cfgparser.load_config(*config_files):
            startup_logger.error("There was a problem with the configuration. "
                                 "Please check above.")
            sys.exit(2)
    except (TypeError, toml.decoder.TomlDecodeError) as err:
        startup_logger.error("Configuration error: %s", str(err))
        sys.exit(2)


def setup_logging(args, cfg, shared):
    """
    Sets up logging for the rest of arbiter based on a given ArgumentParser
    and the configuration.

    args: ArgumentParser()
        The arguments from a argument parser.
    cfg: cfgparser.Configuration()
        The configuration for arbiter.
    shared: cfgparser.Configuration()
        The shared values for arbiter.
    """
    if args.print_logs:
        stream_level = logging.INFO
        if args.quiet:
            stream_level = logging.CRITICAL
        elif args.verbose:
            stream_level = logging.DEBUG
        logger.add_stream(debug_logger, level=stream_level)
        logger.add_stream(service_logger, level=stream_level)

    log_location = cfg.database.log_location
    if not os.path.exists(log_location):
        startup_logger.error("%s directory does not exist!", log_location)
        sys.exit(2)
    elif not os.access(cfg.database.log_location, os.W_OK):
        startup_logger.error("Not enough permissions to write to %s!", log_location)
        sys.exit(2)

    logger.add_rotating_file(
        debug_logger,
        cfg.database.log_location + "/" + shared.debuglog_prefix,
        shared.log_datefmt + ".log",
        cfg.database.log_rotate_period,
        level=logging.DEBUG
    )
    logger.add_rotating_file(
        service_logger,
        cfg.database.log_location + "/" + shared.servicelog_prefix,
        shared.log_datefmt + ".log",
        cfg.database.log_rotate_period,
        fmttr=logger.service_fmttr,
        level=logging.INFO
    )


def pre_run(args):
    """
    Makes preparations before main.run() is ran.
    """
    # Load the config
    setup_config(args.configs)

    # Setup logging (based on the config)
    cfg = cfgparser.cfg
    setup_logging(args, cfg, cfgparser.shared)

    # Turn on accounting
    if args.acct_uid:
        startup_logger.info("Attempting to turn on accounting...")
        try:
            success = permissions.turn_on_cgroups_acct(args.acct_uid)
        except OSError as err:
            logger.debug(err)
            success = False
        if not success:
            startup_logger.error("Failed to turn on accounting. Exiting.")
            sys.exit(2)

    # Make sure cgroup hierarchy already exists
    elif not cinfo.safe_check_on_any_uid(acct_on_for):
        startup_logger.error("cgroup hierarchy doesn't exist (it can be "
                             "turned on via the -a flag). Exiting.")
        sys.exit(2)

    # Check permissions
    startup_logger.info("Checking if arbiter has proper permissions...")
    has_permissions = permissions.check_permissions(
        args.sudo_permissions,
        cfg.general.debug_mode,
        cfg.processes.pss,
        cfg.processes.memsw,
        groupname=cfg.self.groupname,
        min_uid=cfg.general.min_uid
    )
    if not has_permissions:
        startup_logger.error("Arbiter does not have sufficient permissions "
                             "to continue. Exiting.")
        sys.exit(2)


def acct_on_for(uid, controllers=("memory", "cpu")):
    """
    Returns whether accounting is on for a user.

    uid: int
        The uid of the user slice cgroup to check.
    controllers: iter
        The cgroup controllers to check.
    """
    cgroup = cinfo.UserSlice(uid)
    acct_on = all(map(cgroup.controller_exists, controllers))

    # The systemd controller should always exist, if it doesn't the user isn't
    # on the machine. We use this to assert that the checks above aren't
    # failing because the user has logged out, rather than just the acct not
    # being on
    if not acct_on and not cgroup.controller_exists("systemd"):
        raise FileNotFoundError("The user disappeared!")
    return acct_on


if __name__ == "__main__":
    args = arguments()
    pre_run(args)
    import main
    startup_logger.info("Arbiter is starting.")
    sys.exit(main.run(args))
