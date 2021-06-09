# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
A module containing helper objects for various timers.
"""

import time
import datetime


class TimeRecorder:
    """
    Accurately record changes in time.
    """

    def __init__(self):
        self.start_time = time.monotonic()
        self.waittime = 0

    def start_now(self, waittime):
        """
        Starts the time recorder.
        """
        self.waittime = waittime
        self.start_time = time.monotonic()

    def expired(self):
        """
        Returns whether there is any time left.
        """
        return self.delta() <= 0

    def delta(self):
        """
        Returns how much waiting is left.
        """
        return self.waittime - self.time_since_start()

    def time_since_start(self):
        """
        Returns the amount of time since the start time.
        """
        return time.monotonic() - self.start_time


class DateRecorder:
    """
    Accurately record changes in dates.
    """

    def __init__(self):
        self.start_time = datetime.date.today()
        self.waittime = datetime.timedelta(days=0)

    def start_now(self, waittime):
        """
        Starts the time recorder.
        """
        self.start_time = datetime.date.today()
        self.waittime = waittime

    def start_at(self, start_date, waittime):
        """
        Starts the time recorder at a specified datetime.
        """
        self.start_time = start_date
        self.waittime = waittime

    def expired(self):
        """
        Returns whether there is any time left.
        """
        return self.delta() <= 0

    def delta(self):
        """
        Returns how much waiting is left.
        """
        return (self.waittime - self.time_since_start()).days

    def time_since_start(self):
        """
        Returns the amount of time since the start time in a timedelta.
        """
        return datetime.date.today() - self.start_time
