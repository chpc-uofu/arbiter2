# SPDX-License-Identifier: GPL-2.0-only
"""
Logs events and actions that arbiter takes to a database.
"""

import os
import pathlib
import re
from datetime import datetime, timedelta, MINYEAR
import database


def rotated_filename(filename, days, datefmt):
    """
    Returns a filename formatted with a date that is correctly rotated given
    the number of days.

    filename: str
        A filename with a {} in it, where the date goes.
    days: int
        Defines the period of time that things should be changed.
    datefmt: str
        A time.strftime format used to look at previous logs and write out
        new ones.
    """
    latest_log_dt = datetime(MINYEAR, 1, 1)
    fglob = filename.replace("{}", "*")
    # Get all dbs in directory and create datetime obj from names
    path = pathlib.Path(filename).parent
    for db in path.glob(fglob):
        try:
            found_date = re.search(fglob.replace("*", "(.*)"), db.name).group(1)
            found_datetime = datetime.strptime(found_date, datefmt)
            if (found_datetime > latest_log_dt):
                latest_log_dt = found_datetime
        except (AttributeError, ValueError):
            continue

    now = datetime.now()
    if (latest_log_dt + timedelta(days) < now):
        latest_log_dt = now  # Return a new file
    return filename.format(latest_log_dt.strftime(datefmt))


def log_schema():
    """
    Returns the schema used for creating the log database tables. Output is of
    the form [[str, ], [str, ], [str, ]] in the order actions, general, and
    process.
    """
    return [["id INTEGER PRIMARY KEY AUTOINCREMENT", "action", "user",
             "time INTEGER"],
            ["actionid INTEGER", "mem", "cpu", "time INTEGER",
             "FOREIGN KEY(actionid) REFERENCES actions(id) ON DELETE CASCADE"],
            ["actionid INTEGER", "name", "mem", "cpu", "uptime INTEGER",
             "timestamp INTEGER",
             "FOREIGN KEY(actionid) REFERENCES actions(id) ON DELETE CASCADE"]]


def create_log_database(filename, schema, names):
    """
    Create a database for storing log information.

    filename: str
        The path to the database file to be used.
    schema: [[str, ], [str, ], [str, ]]
        The schema for the logging database tables (actions, general, process).
    names: [str, str, str]
        The names for each table.
    """
    database.create_database(filename, schema[0], names[0])
    database.create_database(filename, schema[1], names[1])
    database.create_database(filename, schema[2], names[2])
    return os.path.isfile(os.path.expanduser(filename))


def add_action(action, user, historic_events, timestamp, filename):
    """
    Adds an action to the logging database.

    action: str
        The action that was taken.
    user: str
        The user associated with the action.
    historic_events: collections.deque
        The historic (general) usage of the user (see triggers.py).
    timestamp: int
        The time (epoch) at which the action was taken.
    filename: str
        The name of the logdb file to write out to.
    """
    action_obj = Action(action, user, timestamp)
    for event in historic_events:
        general_obj = General(event["mem"], event["cpu"], event["time"])
        pids = event["pids"]

        # Add each PID object to the action
        for pid in pids:
            process = Process(pids[pid].name, pids[pid].usage["mem"],
                              pids[pid].usage["cpu"], pids[pid].uptime,
                              event["time"])
            action_obj.add_process(process)

        # Add each general object to the action
        action_obj.add_general(general_obj)

    _add_log_entry(action_obj, filename)


def _add_log_entry(action, filename):
    """
    Adds an entry to the logging database. Each entry is an "action" with
    (potentially) multiple "general" historical states, and (potentially)
    multiple "process" states. This allows the database to be searched quickly
    while keeping considerable historical information.

    action: Action(object)
        The action that has been taken.
    filename: str
        The path to the database file to be used.
    """
    # Check for a database file; create it if not found
    if not os.path.isfile(filename):
        table_names = ["actions", "general", "process"]
        create_log_database(filename, log_schema(), table_names)

    # Enable foreign key constraints to have correct secondary tables
    database.execute_command(filename, "pragma foreign_keys = ON")

    # Add the top level of the hierarchy
    cmd = "insert into actions(action, user, time) values(?, ?, ?)"
    result, ident = database.execute_command(filename, cmd, action.action,
                                             action.user, action.timestamp)

    # Add each middle level of the hierarchy
    for general in action.general:
        cmd = "insert into general(actionid, mem, cpu, time) values(?, ?, ?, ?)"
        database.execute_command(filename, cmd, ident, general.mem,
                                 general.cpu, general.time)

    # Add each low level of the hierarchy
    for proc in action.process:
        cmd = ("insert into process(actionid, name, mem, cpu, uptime, "
               "timestamp) values(?, ?, ?, ?, ?, ?)")
        database.execute_command(filename, cmd, ident, proc.name, proc.mem,
                                 proc.cpu, proc.uptime, proc.timestamp)
    return True  # FIXME; catch errors!


# An action (high-level)
class Action(object):
    __slots__ = ["action", "user", "timestamp", "general", "process"]

    def __init__(self, action, user, timestamp):
        self.action = action
        self.user = user
        self.timestamp = timestamp
        self.general = []
        self.process = []

    # General (cgroup)
    def add_general(self, general):
        self.general.append(general)

    # Process-specific
    def add_process(self, process):
        self.process.append(process)


# General usage (mid-level)
class General(object):
    __slots__ = ["mem", "cpu", "time"]

    def __init__(self, mem, cpu, time):
        self.mem = mem
        self.cpu = cpu
        self.time = time


# Process usage (low-level)
class Process(object):
    __slots__ = ["name", "mem", "cpu", "uptime", "timestamp"]

    def __init__(self, name, mem, cpu, uptime, timestamp):
        self.name = name
        self.mem = mem
        self.cpu = cpu
        self.uptime = uptime
        self.timestamp = timestamp
