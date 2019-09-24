# Updating Arbiter

## Versioning

Arbiter is released using [semantic versioning](https://semver.org/) ([major].[minor].[release]).

## Strategies

Regardless of the updating strategy, it may be a good idea to duplicate etc/ and move the changed etc/ to a different location (you'll want to use the -e flag in Arbiter). One such place is etc/sitename, since it is ignored by Git (CHPC uses etc/chpc). There are two main updating strategies:

1. Use Git to clone this repository and use the Git repository. To update, simply do a `git pull`. So as long as the major version doesn't change, you won't have to do anything (besides maybe enable new features). If a major version does come out, you'll still want to do a `git pull`, but you'll have to do some configuring.

2. Download the repository and store a snapshot of the program. To update, simply download a new snapshot and change the configuration (if necessary).

Note that the CHANGELOG.md stores the changes that have been made, as well as what actions need to be taken to update Arbiter (if not specified, nothing needs to be done).
