# Changelog

## Version 1.3.1

**Bugfixes:**

- Fix arbiter failing when it sees a user with no passwd entry.
- Add mitigations to permission checks that are subject to race conditions.
- Fix process counts always starting at 1.
- Prevent arbiter from calling itself out
- Better handle a race condition where a cgroup/pid disappears and reappears in between polling.:

**Changes:**

- Misc code improvements.
- In badness score calculations, use averaged data, rather than instantaneous.
- In high usage warnings, use averaged data, rather than instantaneous.
- Make the configuration the final source for determining the default status (rather than the status database).
- Optionally cap the badness increase based on the max usage at the quota.
- Improve logging of high usage warnings.
- Removed whitelist pattern matching. This was removed since it was a O(n^4) operation every interval and used too much CPU.

## Version 1.3

Initial release.

