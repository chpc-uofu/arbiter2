#!/usr/bin/env python3
import argparse
import cfgparser
from cfgparser import cfg, shared
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

    localhost_emails = set([addr for addr in to_email
                            if addr.find("@localhost") != -1])
    other_emails = set(to_email).difference(localhost_emails)

    # Attempt to send emails
    if localhost_emails:
        actions.send_email(
            subject,
            message,
            list(localhost_emails),
            bcc,
            sender,
            localhost=True,
            reply_to=reply_to
        )

    if other_emails:
        actions.send_email(
            subject,
            message,
            list(other_emails),
            bcc,
            sender,
            localhost=False,
            reply_to=reply_to
        )


def configure(args):
    """
    Configures the program so that it can function correctly.
    """
    os.chdir(args.arbdir)
    insert(args.arbdir)
    if args.etc:
        insert(args.etc)
    else:
        insert("../etc")  # Fallback, using same convention as arbiter.py
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
    Appends a path to the Python path.
    """
    context_path = os.path.dirname(__file__)
    sys.path.insert(0, os.path.abspath(os.path.join(context_path, context)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Arbiter email tester")
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
    parser.add_argument("-e", "--etc",
                        type=str,
                        help="Set the directory in which configurable modules "
                             "are loaded from (e.g. integrations.py). If a "
                             "required module does not exist in the new "
                             "directory, the default module will be loaded "
                             "from ../etc",
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
    # Make the path to files absolute. This makes behavior consistent when
    # changing directories. Otherwise, configuration files would be relative to
    # the arbiter/ directory
    args.configs = [os.path.abspath(path) for path in args.configs]
    configure(args)
    import actions
    main(args, subject=args.subject, message=args.message, to_email=args.to,
         bcc=args.bcc, sender=args.sender, reply_to=args.replyto)
