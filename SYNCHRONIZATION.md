# Arbiter2's Synchronization Mechanism

Since version v2.0.0, Arbiter2 has had the ability to synchronize penalties and the remembrance of penalties across multiple nodes. Understanding how Arbiter2 does this synchronization, and how it handles network and node failures may be of interest to system administrators who use Arbiter2's synchronization capabilities.

Synchronization is optional and can be avoided by not sharing the same database or manually putting each host in their own sync group. When synchronization is not enabled, a local sqlite3 database can be used.

## Communication Mechanism

Fundamentally, Arbiter2 stores it's persistent state (user state and penalties) in a SQL database. By default, this database is a local SQLite3 database on disk (e.g. `logs/statuses.db` in the cloned directory). Because this resides on disk, historically each a Arbiter2 instance has had their own Sqlite3 database.

However, when synchronization capabilities were added, a choice was made to utilize the same SQL database as the mechanism through which different Arbiter2 instances see each other's state and synchronize their state from others. This choice was based on simplicity: this avoids having to deal with hard distributed systems problems, or the requirement for a custom central service.

As such, the model for communication between _synchronized_ Arbiter2 instances is that independent Arbiter2 instances each store their state in a shared network-based SQL database (e.g. anything but SQLite), and peek at the state of other Arbiter2 instances when synchronizing and reconciling their state with other instances. Because of the way this is designed, non-synchronized Arbiter2 instances can still utilize a local SQLite database without issue due to the storing of state being the same between synchronized and non-synchronized instances.

## Sync Groups

To determine which instances synchronize with each other, Arbiter2 has a notion of a synchronization group, that is, a group of Arbiter2 instances _on a particular networked database_ in which penalties and remembrance of penalties (called "occurrences" internally) on one instance can be freely adopted by another instance in that same group. This sync group requires all instances to be able to reach the same network database.

This is configured with the `statusdb_sync_group` name in the `[database]` section of the configuration. Instances that have the same `statusdb_sync_group` name and are configured to use the same database will implicitly synchronize state between each other. The database is configured using the `statusdb_url` option if a non-sqlite database is used (the config defaults to a local SQLite instance at the `log_location`: `../logs/` relative to the `arbiter/` code directory).

### Storage Representation in the Database

The way Arbiter2 instances store their state in the SQL database is by writing each logical state (status, badness) to a per-sync group table in the database. When it comes to synchronizing state between instances, members of the sync group simply look at the other member's state in that shared table. This makes which instances synchronize with other instances implicit based on the instances that write to that table.

It should be noted that the SQL schema used before version 2 differs slightly from the version 2 schema. If an existing v1-based sqlite3 database is used, Arbiter2 gracefully uses that. For this reason, adding synchronization results in penalties and penalty remembrances being reset when migrating to a network-based database.

### Listing Synchronized Hosts in Emails

It may be of interest to system administrators to customize their Arbiter2 email messages with a list of hosts that the sender is syncing with. Email customizations are expected to be done by overriding functions in `etc/integrations.py`. Starting in `v2.0.0`, a list of non-fully qualified hostnames is provided to the `warning_email_body` function that enables a site to customize their warning email message with this information. (this is also why `integrations.py` breaks upon upgrading to `v2.0.0` from `v1.*`)

### Administrative Cleanup upon Instance Removal

_Note: This section only applies when a synchronized instance is removed from a sync group (e.g. when a node is retired)_

As noted in the storage representation subsection above, the way synchronization happens between instances is by having each instance look at the state of other instances within a shared table. This design results in potential side effects when it comes to the removal of a host from a synchronization group. Although the synchronization algorithm will ignore the state of stale instances (see the synchronization algorithm section below), one side-effect of this is that an instance may incorrectly report through emails and logs that it is still synchronizing with the removed instance.

Presently, there is no automated tool or mechanism in Arbiter2 that can remove a host from the corresponding tables in the database. Instead, an administrator must remove these entries manually. The following is a guide on that:

1. First, obtain the name of the sync group and status and badness table from the configuration. These are correspondingly stored in the `statusdb_sync_group`, `statusdb_status_tablename` and `statusdb_badness_tablename` options in the configuration. Some configuration options are hidden in the config when defaulted*. Unless explicitly modified, `statusdb_status_tablename` and `statusdb_badness_tablename` are likely just `status` and `badness`.

2. Remove the instance's corresponding hostname from the `<statusdb_status_tablename>` status table and `<statusdb_badness_tablename>` badness table. For example, with a `general` sync group:

```
DELETE FROM status WHERE hostname='<removed-host>' AND sync_group='<sync-group>';
DELETE FROM badness WHERE hostname='<removed-host>' AND sync_group='<sync-group>';
```

_* These can be viewed using the `arbiter/cfgparser.py` tool with the `--print` flag (the `--eval-specials` is likely also useful here): `python3 ./arbiter/cfgparser CONFIG_LIST... --print`._

### Access Requirements

Arbiter2 requires a user that has the ability to create tables and modify those tables in the SQL database. The creation of state tables is automatically done when an instance is first ran.

## What is Synchronized

It is important to note what specifically is synchronized between instances. Syncing penalties and penalty remembrance is correct at a high-level, but it's more technical than that at a low level. At a low level, the _entire_ status of a user is synchronized between instances. A status includes not just a user's penalty level (called "occurrences" internally), but also their default status group (e.g. `normal` or `admin`), the timestamp for when the user got in penalty, and the timestamp for when they started being forgiven (`occur_timestamp` internally). Frequent updates to the latter can occasionally happen when a user is released from penalty but is still gaining badness, since forgiveness starts when the user started being "good" (zero badness score) after they were removed from penalty. When this does occur, it results in lots of synchronizations and logging since the forgiveness timestamp is frequently being updated.

## Synchronization Algorithm

The synchronization algorithm implemented in Arbiter2 is designed to be reasonably tolerant of host and network failure, while keeping the logic fairly easy reason about. The algorithm works by having each instance evaluate and penalize users on each particular host every period (specifically, every `arbiter_refresh` in the config), writing those per-user statuses (states) for the host out to a shared database table, before finally synchronizing and reconciling each user's status on the host with their statuses on other hosts found in the database.

The reconciliation process is relatively simple. Rather than exchanging or updating parts of a particular status when one status on a host is more "correct" than another, the entire status is replaced with the more "correct" one. The choice of the more "correct" status is solely based on whether one host's user status is more recent, severe and valid than the other. When there are no failures, this process works well since the difference between states on each host is at most the difference between a single event such as a violation, or penalty decrease and the more recent, severe, and valid status follows the intuitive choice. However, this algorithm degrades slightly in the case of network failure, see the next section for details.

It should be noted that in addition to the usual status/state stored on a per-user per-host basis (penalty, historical penalty level, etc), each status also stores an authority tag which is attributed to the host where the user got in penalty on. This tag is used to determine whether a host can send the all-clear nice email when the user is removed from penalty. This tag is reset when the user is removed from penalty so that new violations on other hosts result in emails being sent.

### Handling Failures

There are two types of failure that can happen with Arbiter2:

1. An Arbiter2 instance goes down (cannot update state)

2. There is a network disconnect between an Arbiter2 instance and the database (cannot write out state)

In both cases, all Arbiter2 instances still up in a sync group will proceed in the case of failure. As alluded to above, synchronization is based not on quorum or majority rule, but rather based on each host making their own judgement about the present known statuses on all other hosts and in particular, the time recorded when those statuses were changed. The judgement each host makes is simple: "pick the most recent, severe and valid state possible". The following will happen in each case below:

1. When an Arbiter2 instances goes down, it loses all state that has occurred since it last synchronized with the database. If the last recorded state for the host is still the most "correct" state, all the other instances will carry the state forward and will lower any penalties when they expire. Other hosts may also update the state, if say, the user is penalized on a still-up host. Upon the lowering of a penalty, an email will not be sent by any of these hosts because the authority tag kept along each status is not attributed to any of them. If/when the previously down Arbiter2 instance comes back up, it will briefly adopt it's old state, before immediately reconciling that state with the state of others and taking the most recent, severe, and valid state possible (the one accepted by all other hosts).

2. If a Arbiter2 instance is disconnected from the database, it cannot write out any new state or notify any other Arbiter2 instances of it's state. For the disconnected host, it will proceed with it's own local per-user statuses/state like usual (lowering penalties, putting users in penalty, etc). If however, the disconnected Arbiter2 instance goes down, the modified state since the disconnect is lost. On the contrary, if the Arbiter2 instance reconnects without failing, it will write out it's state to the database before overwriting it's state with others using the same logic as above: "pick the most recent, severe and valid state possible". This may result in a "jolting" effect for user statuses, as they may suddenly be put in penalty on that disconncted host. This reconciled state may also resolve to something different in one or more of the following synchronization, steming from the updating of statuses from other disconnected hosts. After all disconnected hosts write out their states, all instances should adopt the same state within two synchronizations.

## Assumptions

Put together, Arbiter2 makes several important assumptions about it's environment that cannot be violated safely:

1. Only one Arbiter2 instance can exist for a particular hostname\*. In particular, the hostname used is not necessarily fully qualified (it is the same as outputed with the `hostname` command), so it is assumed that a `login1.hpc.institution.edu` and `login1.institution.edu` where each host has a corresponding Arbiter2 instance cannot exist.

   _\*this is techincally possible to do safely by using different databases or sync groups. The emails to users would be confusing however._

2. Time between nodes is kept reasonably up to date with regards to one another (on the order of seconds). Because of the (relative) lack of time precision in violations, timestamps are directly compared across hosts in the same sync group. Timezone-independent epoch is used.

3. [Byzantine behavior](https://en.wikipedia.org/wiki/Byzantine_fault) does not occur. In particular, Arbiter2 has no quorum or majority mechanisms because Arbiter2 assumes that each host is "honest" . A probable byzantine behavior to watch out for is the inclusion of different configurations within a sync group or an invalid manual change by a system administrator in the database (invalid is ambiguous here, but it's hard to define validity; one _invalid_ case would be moving a status' timestamp backwards in time or changing the current status group without updating their timestamps).

4. Each instance in a sync group runs exactly the same code and has the same configuration. In particular, different status groups and expiration timings between nodes in a sync group will either cause hard failures or silent misbehaviors. There is presently no mechanism to detect this issue and Arbiter2 doesn't handle this at all.

5. Network disconnect does not occur for a long period. The algorithm degrades somewhat gracefully when this assumption is broken: if a host reconnects after a disconnect, it and the rest will use the most severe valid (non-expired) penalty status found in the database. However, if a host goes down during a network disconnect, it's state during that network disconnect is lost.

6. Uids and gids always map to the same users and groups between instances.
