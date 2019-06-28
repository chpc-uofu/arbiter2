<img src="logo/arbiter2.png" width="150px" />

# Arbiter2
Arbiter2 monitors and protects interactive nodes with [cgroups](https://en.wikipedia.org/wiki/Cgroups). It records the activity on nodes, automatically sets limits on the resources available to each user, and notifies users and administrators by email when resource quotas are changed.

## Installation
The installation instructions are [available in INSTALL.md](INSTALL.md).

## Changes to this project
To review modifications, see [CHANGELOG.md](CHANGELOG.md).

## Tools for monitoring and managing users

### Querying a user's status
Using both a status database (statuses.db) and the given configuration, [arbstatus.py](tools/arbstatus.py) can look up what status a user is in, as well as his or her status timeout. Run `arbstatus.py --help` to view all the options.

### Checking the configuration
A configuration can be checked for basic valididty using the [cfgparser.py](tools/cfgparser.py) tool. This tool also allows admins to print out the config with hidden and special variables in the resulting configuration.

### Viewing logs
Information in logs can be viewed quickly with the [logsearch](tools/logsearch/logsearch.md) utility.

### Corralling users' processes
Processes can be moved to the appropriate locations in the cgroup hierarchy with [user_corraller.sh](tools/user_corraller.sh). This can be applied to all users with [allusers_corraller.sh](tools/allusers_corraller.sh). More information is available in the installation guide.
