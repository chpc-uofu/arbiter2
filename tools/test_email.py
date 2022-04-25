#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only
import argparse
import shlex
import os
import sys
import toml
import time


def main(args, subject=None, message=None, to_email=None, bcc=None,
         sender=None, reply_to=None):
    """
    Sends a test email message to check configurations.
    """

    # Get the current time and date
    localtime = time.asctime(time.localtime(time.time()))
     # Use configurations for required fields if nothing is provided
    if not subject:
        subject = "Test message"
    if not message:
        message = ("<p>This is a test email. If you are reading this, the "
        "configured  values probably work!</p><p>{}</p><p>Current "
        "configuration: {}</p>").format(localtime, cfg.email)
    if not to_email:
        to_email = cfg.email.admin_emails
    if not sender:
        sender = cfg.email.from_email
    if not reply_to:
        reply_to = cfg.email.reply_to

    # Attempt to send emails
    actions.send_email(
        subject,
        message,
        to_email,
        bcc,
        sender,
        reply_to=reply_to
    )

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
    insert(args.etc)

    import cfgparser
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
    parser = argparse.ArgumentParser(description="Arbiter email tester")
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
    parser.add_argument("-e", "--etc",
                        type=str,
                        help="Set the directory in which configurable modules "
                             "are loaded from (e.g. integrations.py). If a "
                             "required module does not exist in the given "
                             "directory, the default module will be loaded "
                             "from $ARBETC if present or ../etc otherwise.",
                        default=arb_environ.get("ARBETC", "../etc"),
                        dest="etc")
    parser.add_argument("--to",
                        type=str,
                        nargs="+",
                        default=[],
                        help="The users who will receive the test message",
                        dest="to")
    parser.add_argument("--bcc",
                        type=str,
                        nargs="+",
                        default=[],
                        help="Users to be added to BCC headers of messages",
                        dest="bcc")
    parser.add_argument("--from",
                        type=str,
                        default="",
                        help="The sending email address",
                        dest="sender")
    parser.add_argument("--replyto",
                        type=str,
                        default="",
                        help="The reply-to address of emails",
                        dest="replyto")
    parser.add_argument("--message",
                        type=str,
                        default="",
                        help="The message to send, if a custom message is "
                             "desired",
                        dest="message")
    parser.add_argument("--subject",
                        type=str,
                        default="",
                        help="The subject to send, if a custom subject is "
                             "desired",
                        dest="subject")
    args = parser.parse_args()
    bootstrap(args)
    from cfgparser import cfg, shared
    import actions
    main(args, subject=args.subject, message=args.message, to_email=args.to,
         bcc=args.bcc, sender=args.sender, reply_to=args.replyto)
