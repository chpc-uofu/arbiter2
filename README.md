<img src="resources/arbiter2.png" width="150px" />

# Arbiter2
Arbiter2 monitors and protects interactive nodes with [cgroups](https://en.wikipedia.org/wiki/Cgroups). It records the activity on nodes, automatically sets limits on the resources available to each user, and notifies users and administrators by email when users are penalized for using excessive resources. Arbiter2 can also optionally synchronize these penalties and the states of users across interactive nodes. A technical paper has been written on the program and is [available on the ACM Digital Library](https://doi.org/10.1145/3332186.3333043) and the [primary author's homepage](https://dylngg.github.io/resources/arbiterTechPaper.pdf).

Arbiter2 is written by the [University of Utah Center for High Performance Computing](https://www.chpc.utah.edu/), with some contributions from [Idaho National Laboratory](https://inl.gov).

## Installation
The installation instructions are [available in INSTALL.md](INSTALL.md). If synchronization capabilities are used, it is recommended that the [synchronization document](SYNCHRONIZATION.md) be read.

## Changes to this project
To review modifications and to see how to upgrade versions, see [CHANGELOG.md](CHANGELOG.md).

## Tools for monitoring and managing users

The following tools all (optionally) use the `ARBDIR`, `ARBETC` and `ARBCONFIG` environment variables to allow for the tools to be used and moved outside of the tools/ directory without any additional flags. The following can be modified and put in a bashrc/bash\_profile to set these automatically.
```bash
ARBBASEDIR="/usr/local/src/Arbiter2/1.4.0/"
export ARBDIR="$ARBBASEDIR/arbiter"
export ARBETC="$ARBBASEDIR/etc"
export ARBCONFIG="$ARBETC/config.toml $ARBETC/_nomemsw.toml $ARBETC/_noperms.toml"
```

### Querying a user's status
The [arbstatus.py](tools/arbstatus.py) tool can look up what status a user is in, as well as their status timeout. Run `arbstatus.py --help` to view all the options.
```bash
$ ./arbstatus.py
Status:                  admin
Time Left:                 inf
Penalty Occurrences:         0
Default Status:          admin
```

### Checking the configuration
A configuration can be checked for basic validity using the [cfgparser.py](tools/cfgparser.py) tool. This tool also allows admins to print out the config with hidden and special variables in the resulting configuration.

### Getting periodic updates
To get periodic updates about repeat offenders and processes that are seen frequently, use the [arbreport.py](tools/arbreport.py) tool. It allows administrators to see an overview of recent actions taken and can provide summary emails.

### Corralling users' processes
Processes can be moved to the appropriate locations in the cgroup hierarchy with [user_corraller.sh](tools/user_corraller.sh). This can be applied to all users with [allusers_corraller.sh](tools/allusers_corraller.sh). More information is available in the installation guide.
