# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
Logs events and actions that arbiter takes to a database.
"""

import datetime
import logging
import os
import pathlib
import re
import types

import database
import timers

logger = logging.getLogger("arbiter." + __name__)

actions_schema = [
    "id INTEGER PRIMARY KEY AUTOINCREMENT",
    "action",
    "user",
    "time INTEGER"
]
general_schema = [
    "actionid INTEGER",
    "mem",
    "cpu",
    "time INTEGER",
    "FOREIGN KEY(actionid) REFERENCES actions(id) ON DELETE CASCADE"
]
process_schema = [
    "actionid INTEGER",
    "name",
    "mem",
    "cpu",
    "uptime INTEGER",
    "timestamp INTEGER",
    "FOREIGN KEY(actionid) REFERENCES actions(id) ON DELETE CASCADE"
]


class LogDB(database.Database):
    """
    A class for reading and writing from a single logdb.
    """

    def __init__(self, path):
        """
        Initializes an object that connects to a logdb database.

        path: str
            The path to a logdb.
        """
        self.path = path
        super().__init__("sqlite:///" + self.path)
        self.actions_tablename = "actions"
        self.general_tablename = "general"
        self.process_tablename = "process"

    def reset_path(self):
        """
        Updates the LogDB with a new database path.
        """
        self.reset("sqlite:///" + self.path)

    def create_log_database(self):
        """
        Create a database for storing log information.
        """
        self.create_database(actions_schema, self.actions_tablename)
        self.create_database(general_schema, self.general_tablename)
        self.create_database(process_schema, self.process_tablename)

    def add_action(self, action, user, history_iter, timestamp):
        """
        Adds an action to logdb.

        action: str
            The action that was taken.
        user: int, str
            The user (uid) associated with the action.
        history_iter: iter
            An iterator of historic events of the user (see user.py) to log.
        timestamp: int
            The time (epoch) at which the action was taken.
        """
        action_obj = Action(action, str(user), timestamp)
        for event in history_iter:
            general_obj = General(event["mem"], event["cpu"], event["time"])
            pids = event["pids"]

            # Add each process object to the action
            for pid in pids:
                process_obj = Process(pids[pid].name, pids[pid].usage["mem"],
                                      pids[pid].usage["cpu"], pids[pid].uptime,
                                      event["time"])
                action_obj.add_process(process_obj)

            # Add each general object to the action
            action_obj.add_general(general_obj)

        self._add_log_entry(action_obj)

    def _add_log_entry(self, action_obj):
        """
        Adds an entry to logdb. Each entry is an "action" with (potentially)
        multiple "general" historical states, and (potentially) multiple
        "process" states. This allows the database to be searched quickly
        while keeping considerable historical information.

        action_obj: Action
            An action object containing data about the action that has been
            taken.
        """
        # SQL Injection Avoidance: We must use bind params here cause we don't
        #                          trust process names
        # Enable foreign key constraints to have correct secondary tables
        self.execute_command("pragma foreign_keys = ON")

        # Add the top level of the hierarchy
        cmd = (
            "insert into actions(action, user, time) "
            "values(:action, :user, :timestamp)"
        )
        params = {
            "action": action_obj.action,
            "user": action_obj.user,
            "timestamp": action_obj.timestamp
        }
        _, ident = self.execute_command(cmd, params)

        # Add each middle level of the hierarchy
        for general_obj in action_obj.general:
            cmd = (
                "insert into general(actionid, mem, cpu, time) "
                "values(:actionid, :mem, :cpu, :time)"
            )
            params = {
                "actionid": ident,
                "mem": general_obj.mem,
                "cpu": general_obj.cpu,
                "time": general_obj.time
            }
            self.execute_command(cmd, params)

        # Add each low level of the hierarchy
        for process_obj in action_obj.process:
            cmd = (
                "insert into process(actionid, name, mem, cpu, uptime, timestamp) "
                "values(:actionid, :name, :mem, :cpu, :uptime, :timestamp)"
            )
            params = {
                "actionid": ident,
                "name": process_obj.name,
                "mem": process_obj.mem,
                "cpu": process_obj.cpu,
                "uptime": process_obj.uptime,
                "timestamp": process_obj.timestamp
            }
            self.execute_command(cmd, params)
        return True  # FIXME; catch errors!

    def read_actions(self, user=None):
        """
        Returns a list of Action objects for each action. If user is
        specified, actions associated with that user are returned.

        user: int, str
            The user (uid) associated with the action.
        """
        # Enable foreign key constraints to have correct secondary tables
        self.execute_command("pragma foreign_keys = ON")

        # Select actions by user (if given)
        action_dict = {}
        user_constraint = {}
        if user is not None:
            user_constraint = {"user": str(user)}  # where user = ...

        actions_table = self.read_table(self.actions_tablename, **user_constraint)
        for row in actions_table:
            # row = OrderedDict({"id": , "action": , "user": , "time": })
            actionid = int(row.pop("id"))
            action_obj = Action(*row.values())
            action_dict[actionid] = action_obj

        # Go through each action and add associated general and process data
        for actionid, action_obj in action_dict.items():
            action_constraint = {"actionid": actionid}  # where actionid = ...
            general_table = self.read_table(
                self.general_tablename,
                **action_constraint
            )
            for row in general_table:
                # row = OrderedDict({"actionid": , "mem": , "cpu": , "time": })
                row.pop("actionid")
                general_obj = General(*row.values())
                action_obj.add_general(general_obj)

            process_table = self.read_table(
                self.process_tablename,
                **action_constraint
            )
            for row in process_table:
                # row = OrderedDict({"actionid": , "name": , "mem": , "cpu": , "uptime": , "timestamp"})
                row.pop("actionid")
                process_obj = Process(*row.values())
                action_obj.add_process(process_obj)

        return list(action_dict.values())


class RotatingLogDB(LogDB):
    """
    A class for reading and writing from a rotating logdb.
    """

    def __init__(self, path_fmt, rotate_period_days):
        """
        Initializes an object that connects to a logdb database.

        path_fmt: str
            A sqlite database path with a singe "{}" for where the date
            goes in the URL. e.g. "../logs/hostname/log.{}.db"
        rotate_period_days: int
            How many days between database rotations.
        """
        self.path_fmt = path_fmt
        self.date_fmt = "%Y-%m-%d"
        self.rotate_period = datetime.timedelta(days=rotate_period_days)
        self.last_rotation = self._find_last_rotation_date()
        self.path = self._date_path(self.last_rotation)
        super().__init__(self.path)

    def rotate_if_needed(self):
        """
        Rotates the logdb database only if the rotation period has passed and
        returns a new date timer for when logdb should be rotated again.
        """
        today = datetime.date.today()
        next_rotate_date = self.last_rotation + self.rotate_period
        if next_rotate_date < today:
            return self.rotate()

        rotation_timer = timers.DateRecorder()
        rotation_timer.start_at(self.last_rotation, self.rotate_period)
        return rotation_timer

    def rotate(self):
        """
        Rotates the logdb database and returns a new date timer for when logdb
        should be rotated again.
        """
        last_rotation = self.last_rotation
        rotation_date = self._rotate_path()

        self.reset_path()  # Create new db engine
        self.create_log_database()  # Create new file

        rotation_timer = timers.DateRecorder()
        rotation_timer.start_at(rotation_date, self.rotate_period)

        if last_rotation == datetime.date.min:
            logger.info("Failed to find logdb database; creating one at %s",
                        self.path)
        elif last_rotation != rotation_date:
            logger.info("Last logdb rotation was on %s; creating new empty "
                        "database at %s.", last_rotation, self.path)
        else:
            logger.info("Last logdb rotation was on %s; using existing "
                        "database.", last_rotation)

        return rotation_timer

    def _rotate_path(self):
        """
        Rotates the date in the path and returns that date.
        """
        today = datetime.date.today()
        next_rotate_date = self.last_rotation + self.rotate_period

        # We haven't ever created a logdb
        if self.last_rotation == datetime.date.min:
            self.path = self._date_path(today)
            self.last_rotation = today

        # We need a logdb rotation today
        elif next_rotate_date == today:
            self.path = self._date_path(today)
            self.last_rotation = today

        # We missed a logdb rotation
        elif next_rotate_date < today:
            # Create latest date aligned to last date; easier to handle
            # e.g. last = 01/01/20, period = 7, today = 01/17/20 -> 01/14/20
            missed_delta = (today - self.rotate_period) - self.last_rotation
            latest_aligned_date = today - (missed_delta % self.rotate_period)
            self.path = self._date_path(latest_aligned_date)
            self.last_rotation = latest_aligned_date

        return self.last_rotation

    def _date_path(self, date):
        """
        Returns a filepath with a rotated filename.

        date: datetime.date
            A date to use for the url.
        """
        return self.path_fmt.format(date.strftime(self.date_fmt))

    def _find_last_rotation_date(self):
        """
        Returns a datetime.date when the last rotation took place. If no
        rotation has taken place, a minimum date is returned.
        """
        latest_log_date = datetime.date.min
        fglob = os.path.basename(self.path_fmt).replace("{}", "*")
        logdb_dir = os.path.dirname(self.path_fmt)
        path = pathlib.Path(logdb_dir)
        for db in path.glob(fglob):
            try:
                found_date_str = re.search(fglob.replace("*", "(.*)"), db.name).group(1)
                found_date = datetime.datetime.strptime(found_date_str, self.date_fmt).date()
                if found_date > latest_log_date:
                    latest_log_date = found_date
            except (AttributeError, ValueError):
                continue
        return latest_log_date


# Note: The following classes uses types.SimpleNamespace for their pretty
#       __str__() method

# An action (high-level)
class Action(types.SimpleNamespace):

    def __init__(self, action, user, timestamp):
        self.action = action
        self.user = int(user)
        self.timestamp = int(timestamp)
        self.general = []
        self.process = []

    # General (cgroup)
    def add_general(self, general):
        self.general.append(general)

    # Process-specific
    def add_process(self, process):
        self.process.append(process)


# General usage (mid-level)
class General(types.SimpleNamespace):

    def __init__(self, mem, cpu, time):
        self.mem = float(mem)
        self.cpu = float(cpu)
        self.time = int(time)


# Process usage (low-level)
class Process(types.SimpleNamespace):

    def __init__(self, name, mem, cpu, uptime, timestamp):
        self.name = name
        self.mem = float(mem)
        self.cpu = float(cpu)
        self.uptime = int(uptime)
        self.timestamp = int(timestamp)
