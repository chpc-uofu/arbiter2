# SPDX-FileCopyrightText: Copyright (c) 2019-2020 Center for High Performance Computing <helpdesk@chpc.utah.edu>
#
# SPDX-License-Identifier: GPL-2.0-only

"""
A collection of methods for getting and applying statuses. Statuses files are
stored in a database at the configured location.
"""
import collections
import logging

import badness
import database
import statuses
import sysinfo
import timers
from cfgparser import cfg

logger = logging.getLogger("arbiter." + __name__)


status_schema = [
    "uid INTEGER NOT NULL UNIQUE",
    "current_status TEXT NOT NULL",
    "default_status TEXT NOT NULL",
    "occurrences INTEGER NOT NULL",
    "timestamp INTEGER NOT NULL",
    "occurrences_timestamp INTEGER NOT NULL",
    "PRIMARY KEY(uid)"
]
status_schema_v2 = [
    "uid INTEGER NOT NULL",
    "current_status TEXT NOT NULL",
    "default_status TEXT NOT NULL",
    "occurrences INTEGER NOT NULL",
    "timestamp INTEGER NOT NULL",
    "occurrences_timestamp INTEGER NOT NULL",
    # Linux defines hostnames to be up to 64 bytes (man 2 gethostname)
    "hostname VARCHAR(64) NOT NULL",
    "CONSTRAINT same_user PRIMARY KEY(uid, hostname)"
]
status_schema_v3 = [
    "uid INTEGER NOT NULL",
    "current_status TEXT NOT NULL",
    "default_status TEXT NOT NULL",
    "occurrences INTEGER NOT NULL",
    "timestamp INTEGER NOT NULL",
    "occurrences_timestamp INTEGER NOT NULL",
    # Linux defines hostnames to be up to 64 bytes (man 2 gethostname)
    "hostname VARCHAR(64) NOT NULL",
    "sync_group VARCHAR(64)",
    "CONSTRAINT same_user PRIMARY KEY(uid, hostname)"
]

badness_schema = [
    "uid INTEGER NOT NULL UNIQUE",
    "timestamp INTEGER NOT NULL",
    "cpu_badness REAL NOT NULL",
    "mem_badness REAL NOT NULL",
    "PRIMARY KEY(uid)"
]
badness_schema_v2 = [
    "uid INTEGER NOT NULL",
    "timestamp INTEGER NOT NULL",
    "cpu_badness REAL NOT NULL",
    "mem_badness REAL NOT NULL",
    "hostname VARCHAR(64) NOT NULL",
    "CONSTRAINT same_user PRIMARY KEY(uid, hostname)"
]
badness_schema_v3 = [
    "uid INTEGER NOT NULL",
    "timestamp INTEGER NOT NULL",
    "cpu_badness REAL NOT NULL",
    "mem_badness REAL NOT NULL",
    "hostname VARCHAR(64) NOT NULL",
    "sync_group VARCHAR(64)",
    "CONSTRAINT same_user PRIMARY KEY(uid, hostname)"
]
# So apparently trying to catch sqlalchemy.exc.DBAPIError and
# sqlalchemy.exc.SQLAlchemyError, which are the base clases for all the errors
# in sqlalchemy engines, fails to adequately catch pymysql errors for some
# reason (Just try dropping a table and you'll see). This sucks since we have
# to use the base Exception, which is equivalent to giving a blank check to
# whatever errors we encounter, programming or operationally. This results in
# us having to silence non database errors that occur, which is kinda unideal
# since it makes it much harder to deal with non database problems outside of
# the initial source of the problem.
# FIXME: Prove that the previous statement is wrong with more testing and
#        googling ^
common_db_errors = Exception


class StatusDB(database.Database):
    """
    A class for reading from statusdb.
    """

    def __init__(self, url, status_tablename, badness_tablename,
                 cfg_db_consistency=False):
        """
        Initializes an object that connects to a statusdb database.

        url: str
            The database URL for connecting to statusdb.
        status_tablename: str
            The status tablename in the given statusdb.
        badness_tablename: str
            The badness tablename in the given statusdb.
        cfg_db_consistency: bool
            Whether or not to ensure that for every user's status, the default
            status group from the database matches that user's configured
            default status group. See statuses.Status().enforce_cfg_db_consistency()
            for details. This may cause unexpected results relating to
            differences between the database and the configuration if this is
            set to true outside of the configuration context that was used to
            put a user in the statusdb (typically from a Arbiter instance).
        """
        super().__init__(url)
        self.status_tablename = status_tablename
        self.badness_tablename = badness_tablename
        self.cfg_db_consistency = cfg_db_consistency
        # Track what is in the database so we can figure out whether old
        # values need to be deleted
        self.stored_badness_uids = set()
        self.stored_status_uids = set()
        # Track which hosts we last synced with; used in known_syncing_hosts()
        self.last_known_syncing_hosts = set()
        self.sync_group = cfg.database.statusdb_sync_group

    def status_and_badness_tablenames(self):
        """
        Returns both the status and badness tablenames used by this StatusDB
        instance.
        """
        return self.status_tablename, self.badness_tablename

    def is_v2_badness_table(self):
        """
        Returns if there is a v2 status table in the given database. May
        raise an error if the table or database doesn't exist.
        """
        return self.column_in_table(self.badness_tablename, "hostname")

    def is_v3_badness_table(self):
        """
        Returns if there is a v3 status table in the given database. May
        raise an error if the table or database doesn't exist.
        """
        return self.column_in_table(self.badness_tablename, "sync_group")

    def is_v2_status_table(self):
        """
        Returns the name there is a v2 status table in the given database. May
        raise an error if the table or database doesn't exist.
        """
        return self.column_in_table(self.status_tablename, "hostname")

    def is_v3_status_table(self):
        """
        Returns the name there is a v2 status table in the given database. May
        raise an error if the table or database doesn't exist.
        """
        return self.column_in_table(self.status_tablename, "sync_group")

    def read_status(self):
        """
        Return a dictionary of uids (int) with their corresponding status and
        whether it was successful in reading from the database.

        >>> self.read_status()
        {1001: statuses.Status(current="penalty1", default="normal", occurrences=0,
                      timestamp=1534261840, occur_timestamp=1534261840)}
        """
        user_status_host_dict = self.read_raw_status()

        user_statuses = {}
        for uid, status_host_dict in user_status_host_dict.items():
            user_statuses[uid] = statuses.lookup_empty_status(uid)
            user_statuses[uid].resolve_with_other_hosts(status_host_dict)
            self.stored_status_uids.add(uid)

        return user_statuses

    def read_raw_status(self):
        """
        Return a dictionary of uids (int) with a dictionary containing each of
        their statuses on each hostname they have a record for.

        >>> self.read_raw_status()
        {1001: {"hostname": statuses.Status(current="penalty1", default="normal",
                                     occurrences=0, timestamp=1534261840,
                                     occur_timestamp=1534261840)}}
        """
        known_syncing_hosts = set()

        table = self.read_table(self.status_tablename)
        status_dict = collections.defaultdict(dict)
        for row in table:  # table -> {col: value, ...}; ordered
            if row["sync_group"] != self.sync_group:
                continue
            uid = int(row.pop("uid"))
            hostname = row.pop("hostname")
            known_syncing_hosts.add(hostname)

            # current, default, occurrences, timestamp, occur_timestamp
            status = statuses.Status(row["current_status"], row["default_status"], row["occurrences"], row["timestamp"], row["occurrences_timestamp"], authority=hostname)
            if self.cfg_db_consistency:
                status.enforce_cfg_db_consistency(uid)

            status_dict[uid][hostname] = status
            if hostname == sysinfo.hostname:
                self.stored_status_uids.add(uid)

        # Do last in case of failure
        self.last_known_syncing_hosts = known_syncing_hosts
        return status_dict

    def write_status(self, status_dict):
        """
        Writes a dictionary of users with their corresponding status.

        status_dict: dict
            A dictionary of users with their status to update the database with.
        """
        new_status_dict = dict()
        for uid, status in status_dict.items():
            if not self._should_be_in_database(status):
                # There may be an existing status in the database, want to
                # make sure that is deleted upon no longer needing status
                # updated (e.g. when authority us -> other). However,
                # given not _should_be_in_database is quite commonly true,
                # check for whether we know it to be in the database first
                # before deleting
                if uid in self.stored_status_uids:
                    self._remove_status(uid)
                continue

            new_status_dict[uid] = {sysinfo.hostname: status}

        self.write_raw_status(new_status_dict)

    def write_raw_status(self, status_dict):
        """
        Writes a dictionary of users with their corresponding status.

        status_dict: dict
            A dictionary of users with their status to update the database with.
        """
        status_columns = ("uid, current_status, default_status, occurrences, "
                          "timestamp, occurrences_timestamp, hostname, "
                          "sync_group")

        # SQL Injection Avoidance: we can trust our own values
        inserts = []
        insert = "REPLACE INTO {} ({}) VALUES({}, '{}', '{}', {}, {}, {}, '{}', '{}');"
        for uid, host_status in status_dict.items():
            for hostname, status in host_status.items():
                inserts.append(insert.format(
                    self.status_tablename,
                    status_columns,
                    uid,
                    status.current,
                    status.default,
                    status.occurrences,
                    status.timestamp,
                    status.occur_timestamp,
                    hostname,
                    self.sync_group
                ))

        self.execute_commands(inserts, [{}] * len(inserts))

    def get_status(self, uid):
        """
        Returns the given user's status in statusdb. Returns an empty status
        if the user is not in the database.

        uid: int
            The user's uid.

        >>> self.get_status(1001)
        statuses.Status(current="normal", default="normal", occurrences=0,
                        timestamp=1534261840, occur_timestamp=1534261840)
        """
        # SQL Injection Avoidance: we can trust our own values
        uid = int(uid)
        status = statuses.lookup_empty_status(uid)
        constraints = {"uid": str(uid), "sync_group": self.sync_group}
        table = self.read_table(self.status_tablename, **constraints)
        if not table:
            return status

        status_host_dict = {}
        for entry in table:
            hostname = entry["hostname"]
            status_host_dict[hostname] = statuses.Status(
                entry["current_status"],
                entry["default_status"],
                entry["occurrences"],
                entry["timestamp"],
                entry["occurrences_timestamp"],
                authority=hostname
            )
        status.resolve_with_other_hosts(status_host_dict)

        if self.cfg_db_consistency:
            status.enforce_cfg_db_consistency(uid)

        self.stored_status_uids.add(uid)
        return status

    def set_status(self, uid, new_status):
        """
        Sets the user's status in the statusdb.

        uid: int
            The user's uid.
        new_status: statuses.Status()
            The new status of the user.
        """
        self.write_status({uid: new_status})

    def _should_be_in_database(self, status):
        """
        Returns whether the status should not be in statusdb.

        uid: int
            The user's uid.
        status: statuses.Status()
            The status of the user.
        """
        # We have an invariant that no non-authoritative (penalty statuses
        # that we adopt from another host when syncing) statuses should be
        # kept in the database. This is so if this host fails, then it will
        # not presume that it's database entries are authoritative (i.e.
        # belong to this host). This could be tracked with a authoritative
        # column, but it's simplier to just not have non-authoritative entries
        # in the database
        #
        # (recall, if a host is authoritative, it will send emails to the user;
        #  if there are multiple authoritative hosts (where the user is in
        #  penalty), then the user will get multiple emails, which we don't
        #  want.)
        return status.authoritative()

    def _remove_status(self, uid):
        """
        Removes the user from statusdb.

        uid: int
            The user's uid.
        """
        uid = int(uid)

        # SQL Injection Avoidance: we can trust our own values
        remove = 'DELETE FROM {} WHERE uid = {} AND hostname = "{}" AND sync_group = "{}";'.format(
            self.status_tablename,
            uid,
            sysinfo.hostname,
            self.sync_group
        )
        self.execute_command(remove)
        self.stored_status_uids.discard(uid)

    def cleanup_status(self):
        """
        Ensures that no unnecessary statuses are stored in statusdb. This is
        needed since Arbiter failures may cause some unnecessary statuses to
        be left in statusdb.
        """
        # Basically, read_status resolves statuses, so if after a resolution a
        # status is no longer needed for this host, remove it
        user_status_dict = self.read_status()

        for uid, status in user_status_dict.items():
            if not self._should_be_in_database(status):
                self._remove_status(uid)

    def read_badness(self):
        """
        Return a dictionary of uids (int) with a tuple containing a dictionary
        of their badness and a timestamp when their badness was last updated.

        >>> self.read_badness()
        {1001: ({"cpu": 0.0, "mem": 0.0}, 683078400)}
        """
        host_constraint = {"hostname": sysinfo.hostname}
        table = self.read_table(self.badness_tablename, **host_constraint)
        user_badness = {}
        for entry in table:  # table -> {col: value, ...}; ordered
            if entry["sync_group"] != self.sync_group:
                continue
            uid = int(entry["uid"])
            user_badness[uid] = badness.Badness(
                cpu=entry["cpu_badness"],
                mem=entry["mem_badness"],
                timestamp=entry["timestamp"]
            )
            self.stored_badness_uids.add(uid)

        return user_badness

    def write_badness(self, badness_dict):
        """
        Writes a dictionary of users with their corresponding badness and a
        timestamp when the badness score was last updated.

        badness_dict: dict
            A dictionary of users with their corresponding badness

        >>> self.write_badness(
                {1000:  badness.Badness(cpu=1.2,mem=1,timestamp=683078400)},
                ...
            )
        """
        badness_columns = "uid, timestamp, cpu_badness, mem_badness, hostname, sync_group"

        # SQL Injection Avoidance: we can trust our own values
        inserts = []
        insert = "REPLACE INTO {}({}) VALUES({}, {}, {}, {}, '{}', '{}');"
        for uid, badness_obj in badness_dict.items():
            if not self._needs_badness_updated(badness_obj):
                # Again, if badness doesn't need updating then we should
                # remove the possible invalid badness record (e.g. nonzero
                # badness is stored in db, user now has zero badness, now
                # need to remove so user doesn't inherit old badness upon
                # restart). First check if known to be in database however
                # since not _needs_badness_updated is quite common.
                if uid in self.stored_badness_uids:
                    self.remove_badness(uid)
                continue

            self.stored_badness_uids.add(uid)
            inserts.append(insert.format(
                self.badness_tablename,
                badness_columns,
                int(uid),
                badness_obj.last_updated(),
                badness_obj.cpu,
                badness_obj.mem,
                sysinfo.hostname,
                self.sync_group
            ))
        self.execute_commands(inserts, [{}] * len(inserts))

    def set_badness(self, uid, badness_obj):
        """
        Add a user's badness to the badness table if the score is nonzero. The
        previous badness will be overridden if a previous badness exists.

        uid: int
            The user's uid..
        badness_obj: badness.Badness()
            The user's corresponding badness object to set.
        """
        self.write_badness({uid: badness_obj})

    def _needs_badness_updated(self, badness_obj):
        """
        Returns whether the badness should be in statusdb.

        uid: int
            The user's uid.
        badness_obj: badness.Badness()
            The user's corresponding badness object to evaluate.
        """
        return badness_obj.is_bad()

    def remove_badness(self, uid):
        """
        Removes a user's badness score from the badness table.

        Note: unlike _remove_status which is private, we call this when we
              import old badness scores, so it needs to be "public"

        uid: int
            The user's uid associated with the properties.
        """
        remove = 'DELETE FROM {} WHERE uid = {} AND sync_group = "{}";'.format(self.badness_tablename, int(uid), self.sync_group)
        self.execute_command(remove)
        self.stored_badness_uids.discard(uid)

    def cleanup_badness(self):
        """
        Ensures that no unnecessary badness scores are stored in statusdb.
        This is needed since Arbiter failures may cause some unnecessary
        badness scores to be left in statusdb.
        """
        user_badness_dict = self.read_badness()

        for uid, badness_obj in user_badness_dict.items():
            if not self._needs_badness_updated(badness_obj):
                self._remove_badness(self, uid)

    def known_syncing_hosts(self):
        """
        Returns a set of hosts that we last successfully synchronized from,
        including our own host.
        """
        last_known_syncing_hosts = self.last_known_syncing_hosts.copy()
        # Ensure our host is in there
        last_known_syncing_hosts.add(sysinfo.hostname)
        return last_known_syncing_hosts

    def create_status_table(self):
        """
        Creates a status table.
        """
        self.create_database(
            status_schema_v3,
            self.status_tablename
        )

    def create_badness_table(self):
        """
        Creates a badness table.
        """
        self.create_database(
            badness_schema_v3,
            self.badness_tablename
        )

    def create_status_database_if_needed(self):
        """
        Creates a tables for storing statusdb information only if non-existent.
        Returns whether the database already existed and whether a migration
        occurred.
        """
        was_created = False
        did_migrate_schema = False
        try:
            is_v2_badness_schema = self.is_v2_badness_table()
            is_v3_badness_schema = self.is_v3_badness_table()
            if not is_v3_badness_schema:
                # Added sync_group column in v3; want to migrate so we don't
                # have to have special logic to deal with old v1 and v2 schemas
                self.execute_command(f"ALTER TABLE {self.badness_tablename} RENAME TO old_{self.badness_tablename}")
                did_migrate_schema = True
                raise database.NoSuchTableError

            # Up-to-date schema exists; cleanup old sync groups.
            cleanup_old_sync_groups = 'DELETE FROM {} WHERE hostname = "{}" AND sync_group != "{}";'.format(self.badness_tablename, sysinfo.hostname, self.sync_group)
            self.execute_command(cleanup_old_sync_groups)
        except database.NoSuchTableError:
            logger.debug("Badness table does not exist; creating it")
            self.create_badness_table()
            was_created = True

        try:
            is_v2_status_schema = self.is_v2_status_table()
            is_v3_status_schema = self.is_v3_status_table()
            if not is_v3_status_schema:
                self.execute_command(f"ALTER TABLE {self.status_tablename} RENAME TO old_{self.status_tablename}")
                did_migrate_schema = True
                raise database.NoSuchTableError

            # Up-to-date schema exists; cleanup old sync groups.
            cleanup_old_sync_groups = 'DELETE FROM {} WHERE hostname = "{}" AND sync_group != "{}";'.format(self.status_tablename, sysinfo.hostname, self.sync_group)
            self.execute_command(cleanup_old_sync_groups)
        except database.NoSuchTableError:
            logger.debug("Status table does not exist; creating it")
            self.create_status_table()
            was_created = True

        return was_created, did_migrate_schema

    def synchronize_status_from_ourself(self, user_statuses):
        """
        Updates this host's user statuses entries based on this host's
        statuses in the database.

        This is a special case of synchronize_status_from_other_hosts, where
        we want to adopt any external changes that happen in the database
        outside of arbiter (e.g. arbupdate.py).

        The normal resolution process in *from_other_hosts is not suited for
        resolving with ourselves because valid penalties take priority over
        everything else (e.g. if we are in penalty and externally someone
        removes us from penalty, we want the non-penalty to take effect).

        user_statuses: dict
            A dictionary of statuses, where the key is a uid and the value is
            their status.
        """
        raw_host_statuses = self.read_raw_status()
        modified_user_statuses = {}
        for uid, curr_status in user_statuses.items():
            if uid not in raw_host_statuses:
                continue
            if sysinfo.hostname not in raw_host_statuses[uid]:
                continue

            old_status = curr_status.copy()
            database_status = raw_host_statuses[uid][sysinfo.hostname]
            was_database_choosen = curr_status.resolve_with_ourself(database_status)
            if was_database_choosen:
                logger.debug(
                    "Database sync: %s's status on %s (%s) is being replaced "
                    "with their own status in the database (%s)",
                    uid,
                    sysinfo.hostname,
                    str(old_status),
                    str(curr_status)
                )

            modified_user_statuses[uid] = curr_status

        self.write_status(modified_user_statuses)

    def synchronize_status_from_other_hosts(self, user_statuses):
        """
        Updates this host's user status entries based on the user's other
        status entries from different hosts. The updated status comes from the
        last updated entry from all the hosts, given that other hosts have
        kept things up to date. Returns a dictionary of uids with the hostname
        that the status was updated from (e.g. an empty dictionary if no
        synchronization was done).

        user_statuses: dict
            A dictionary of statuses, where the key is a uid and the value is
            their status.
        """
        # Synchronization Design Notes:
        #
        # The syncronization algorithm implemented here and in
        # resolve_with_other_hosts aims to be reasonably tolerant of other
        # host failures while attempting to keep the logic as simple as
        # possible. It assumes several things about it's environment:
        #   1. Timestamps are kept reasonably up to date with regards to one
        #      another (on the order of seconds).
        #   2. Network disconnect (host still being up) does not occur for a
        #      long period. The algorithm degrades somewhat gracefully when
        #      this assumption is broken: if a host reconnects after a
        #      disconnect, it and the rest will use the most up to date
        #      status, assuming the instance hasn't crashed (resulting in
        #      the host's more recent changes to statuses being lost).
        #   3. Each instance runs exactly the same code and has the same
        #      configuration.
        #   4. Uids always map to the same users between instances.
        #
        # Here's how it works:
        #
        # We'll do our normal evaluations of users every interval, but after
        # those evaluations, we'll then look at what other hosts believe to be
        # the truth and reconcile each status individually from there based on
        # the timestamps of the current status group and ocurrences changes.
        # The jist of the reconciliation is that we _always_ pick the most up
        # to date status.
        #
        # The key to the resilience and correctness of this algorithm in spite
        # of both network and host failures is the fact that each host
        # independently adjusts their statuses before syncing. This ensures
        # that if the host where a penalty was raised on crashes, the user on
        # other hosts won't get stuck with penalty quotas forever.
        #
        # To keep track of who needs to send emails, an authority host is
        # kept and reflects the host where a particular penalty originated.
        # We only keep our authoritative statuses in the database.

        # Note: There is technically a race condition here between reading
        #       other host's values and writing our own based on those
        #       values (other hosts may update their entries during that
        #       period). I _think_ this is actually fine since a) we only write
        #       to our own per-host slice b) our reads and writes are atomic and
        #       most importantly c) in our write, we update timestamps based on
        #       entries from our read, so if a resolved user's status changes
        #       between our read and write, our rewrite won't change that
        #       resolution since other hosts will simply see our entry as out of
        #       date. Furthermore because we sync every arbiter_refresh, our
        #       entry will only be out of date for roughly arbiter_refresh
        #       seconds, which seems reasonable for our needs.

        # Dictionary of per-user dictionaries of per-host statuses (a host
        # entry is not always present)
        user_status_host_dict = self.read_raw_status()

        modified_user_hosts = {}
        for uid, status in user_statuses.items():
            old_status = status.copy()
            was_empty = old_status.is_empty(uid)
            status_host_dict = user_status_host_dict.get(uid, {})
            repl_hostname = status.resolve_with_other_hosts(status_host_dict)

            # status has not changed, don't mark as a update
            if old_status.strictly_equal(status):
                continue

            modified_user_hosts[uid] = repl_hostname
            if not was_empty and status.is_empty(uid):
                logger.debug(
                    "Database sync: %s's status on %s (%s) is being restored to "
                    "their empty/default.",
                    uid,
                    sysinfo.hostname,
                    str(status),
                )
            elif repl_hostname == sysinfo.hostname:
                logger.debug(
                    "Database sync: %s's status on %s (%s) is being updated to %s",
                    uid,
                    sysinfo.hostname,
                    str(old_status),
                    str(status)
                )
            else:
                logger.debug(
                    "Database sync: %s's status on %s (%s) is being replaced "
                    "with %s's (%s)",
                    uid,
                    sysinfo.hostname,
                    str(old_status),
                    repl_hostname,
                    str(status)
                )

        modified_statuses = {
            uid: user_statuses[uid]
            for uid in modified_user_hosts
        }
        self.write_status(modified_statuses)
        return modified_user_hosts


class StatusDBCleaner:
    """
    Periodically cleans up statusdb. This is required since we have
    constraints such as non-authoritative statuses not being in statusdb or
    nonzero badness scores being in statusdb that cannot be assured upon
    Arbiter or database connection failure.
    """

    def __init__(self, statusdb_obj, interval):
        """
        Initializes the statusdb cleaner
        """
        self.statusdb_obj = statusdb_obj
        self.timer = timers.TimeRecorder()
        self.interval = interval
        self.timer.start_now(self.interval)

    def cleanup_if_needed(self):
        """
        Cleans up the status database if the internal timer has expired.
        Resets the timer if cleaned up.
        """
        if not self.timer.expired():
            return

        try:
            self.statusdb_obj.cleanup_status()
            self.statusdb_obj.cleanup_badness()

            # Only restart timer if we succeeded
            self.timer.start_now(self.interval)
        except common_db_errors as err:
            logger.debug("Failed to cleanup statusdb; will try again: %s", err)


def lookup_tablenames():
    """
    Returns the configured status and badness tablenames.
    """
    return ("status", "badness")
    #sync_group = cfg.database.statusdb_sync_group
    #if sync_group != "":
    #    tablename_ext = "_" + sync_group
    #else:
    #    tablename_ext = ""

    #return (
    #    # status_syncgroup if sync group is defined, else status
    #    "status" + tablename_ext,
    #    # Note that badness scores are currently not synchronized, but we'll
    #    # still seperate the tables for cleanness
    #    "badness" + tablename_ext
    #)


def lookup_statusdb(statusdb_url=None, cfg_db_consistency=False):
    """
    Returns a new StatusDB object based on the configured values.

    statusdb_url: str
        An optional stautsdb_url to use rather than the configuration.
    cfg_db_consistency: bool
        Whether or not to ensure that for every user's status, the default
        status group from the database matches that user's configured default
        status group. See statuses.Status().enforce_cfg_db_consistency() for
        details. This may cause unexpected results relating to differences
        between the database and the configuration if this is set to true
        outside of the configuration context that was used to put a user in
        the statusdb (typically from a Arbiter instance).
    """
    if statusdb_url is None:
        statusdb_url = "sqlite:///{}/statuses.db".format(cfg.database.log_location)
        if cfg.database.statusdb_url != "":
            statusdb_url = cfg.database.statusdb_url

    status_tablename, badness_tablename = lookup_tablenames()

    return StatusDB(
        statusdb_url,
        status_tablename,
        badness_tablename,
        cfg_db_consistency=cfg_db_consistency
    )
