# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
Classes and methods related to checking an exit file for changes.
"""

import datetime
import grp
import logging
import os

from cfgparser import cfg

logger = logging.getLogger("arbiter." + __name__)


class ExitFileWatcher:
    """
    Watches for changes to the given file given that the file is owned by
    the groupname specified in the configuration.
    """

    def __init__(self, filepath):
        """
        Initializes the watcher with the given filepath.

        filepath: str
            The path to the file to watch
        """
        self.filepath = os.path.abspath(filepath)
        self.group_owner = cfg.self.groupname
        try:
            self.last_update = self.modtime()
        except OSError:
            self.last_update = 0

    def owned_by_group(self):
        """
        Returns whether the exit file is owned by the group specified in the
        configuration. May raise OSError if the exit file does not exist.
        """
        gid = os.stat(self.filepath).st_gid
        return grp.getgrgid(gid).gr_name == self.group_owner

    def modtime(self):
        """
        Returns the modtime of the exit file. May raise OSError if the exit
        file does not exist.
        """
        return os.path.getmtime(self.filepath)

    def has_been_updated(self):
        """
        Returns whether the exit file has been updated and is owned by the
        group name specified in the configuration.
        """
        try:
            if not self.owned_by_group():
                return False

            update_time = self.modtime()
            updated = update_time > self.last_update
            if updated:
                logger.error(
                    "Exit file %s was updated at %s; exiting",
                    self.filepath,
                    datetime.datetime.utcfromtimestamp(update_time).isoformat()
                )
            return updated
        except OSError:  # e.g. file doesn't exist
            return False
