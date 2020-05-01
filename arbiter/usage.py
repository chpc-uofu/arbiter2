# SPDX-License-Identifier: GPL-2.0-only
"""
A module that contains a base class and methods related to storing usage
information. This module is meant to be used for creating unique objects that
can be used to collect, store and process information. These objects follow the
following philosophy:

The collection of usage metrics is done by creating a Instance(), which stores
instantaneous values upon creation. This Instance() should then be able to be
divided by another Instance() (by overriding __truediv__) to create a Static()
object, which takes the two instantaneous values and merges them together into
human readable usage values. This Static() object should be able to do
appropriate arithmetic with another Static() object (by inheriting from the
Usage() object). In order to facilitate later use of a object (e.g. getting
the uptime of a process, or setting quotas of a cgroup), Instance() and
Static() objects should inherit from a object that contains methods and
properties to facilitate this. Thus, the inheritance should follow this
pattern:

                            _Facilitator_()
                                   ^
                                   |       Usage()
                               ____|____      ^
                               |       |      |
           _Facilitator_Instance()   Static_Facilitator_()

The _Facilitator_() should be named appropriately (e.g. Process()).

Design Choices:
The process of creating a Instance() object and dividing into a Static()
object comes from the fact that when collecting usage information in Linux,
things like CPU usage cannot be directly collected. For the CPU usage example,
the information given is cputime, which must be compared to the cputime at
another point in time in order to calculate the % of usage a user was using
during that time period. This design makes this possible and is a little
cleaner than other designs that have been done (though it isn't perfect).

Other Notes:
Instance() objects may/should throw exceptions if information cannot be
collected in __init__. The _Facilitator_() should only do this if necessary.

Unless the __init__() args of child Static() objs are the same, it is
recommended that __init__() passes **kwargs to it's Static() obj parent.
With this, eventually the kwargs will be passed into the Usage() obj, which
will set kwargs as object variables. One reason for doing things this way is
that it allows for using a parent's operators in child operators, because
parents can construct child objects using it's own knowledge, but still set
child properties through kwargs. This is different from the traditional
approach of overriding the all of operators and not using the parent's
operators.
"""

metrics = {"cpu": 0.0, "mem": 0.0}


class Usage(object):
    """
    Usage of something.
    """

    def __init__(self, **kwargs):
        """
        Initializes a Usage object.
        """
        self.usage = metrics.copy()
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        return "<{}: {}>".format(type(self).__name__, self.usage)

    def __str__(self):
        properties = []
        for prop, value in vars(self).items():
            properties.append("{}: {}".format(prop, value))
        return str(type(self).__name__) + " " + ", ".join(properties)

    def __lt__(self, other):
        if isinstance(other, Usage):
            return sum(self.usage.values()) < sum(other.usage.values())
        return super().__lt__(other)

    def __le__(self, other):
        if isinstance(other, Usage):
            return sum(self.usage.values()) <= sum(other.usage.values())
        return super().__le__(other)

    def __gt__(self, other):
        if isinstance(other, Usage):
            return sum(self.usage.values()) > sum(other.usage.values())
        return super().__gt__(other)

    def __ge__(self, other):
        if isinstance(other, Usage):
            return sum(self.usage.values()) >= sum(other.usage.values())
        return super().__ge__(other)

    def __add__(self, other):
        if isinstance(other, type(self)):
            kwargs = vars(self).copy()
            kwargs["usage"] = {
                metric: usage + other.usage[metric]
                for metric, usage in self.usage.items()
            }
            return type(self)(**kwargs)
        else:
            kwargs = vars(self).copy()
            kwargs["usage"] = {
                metric: usage + other
                for metric, usage in self.usage.items()
            }
            return type(self)(**kwargs)

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        if isinstance(other, type(self)):
            kwargs = vars(self).copy()
            kwargs["usage"] = {
                metric: usage - other.usage[metric]
                for metric, usage in self.usage.items()
            }
            return type(self)(**kwargs)
        else:
            kwargs = vars(self).copy()
            kwargs["usage"] = {
                metric: usage - other
                for metric, usage in self.usage.items()
            }
            return type(self)(**kwargs)

    def __rsub__(self, other):
        return self.__sub__(other)

    def __truediv__(self, other):
        kwargs = vars(self).copy()
        kwargs["usage"] = {
            metric: usage / other for metric, usage in self.usage.items()
        }
        return type(self)(**kwargs)

    def __floordiv__(self, other):
        kwargs = vars(self).copy()
        kwargs["usage"] = {
            metric: usage // other for metric, usage in self.usage.items()
        }
        return type(self)(**kwargs)


def combine(*instances):
    """
    Combines the Instance() objects together into len(instances)-1 Static()
    objects and returns a list of those Static() objects. If there is only one
    instance, a empty list is returned.

    *instances: Instance()
        Instance objects.
    """
    static_objs = []
    iter_instances = iter(instances)
    prev_instance = next(iter_instances)
    for instance in iter_instances:
        static_objs.append(prev_instance / instance)
        prev_instance = instance
    return static_objs


def average(*statics, by=None):
    """
    Averages the Static() objects together.

    *static: Static()
        Static objects.
    divby: None or int
        What to average by. If None, defaults to the length of the usages.
    """
    return sum(statics) / (by if by else len(statics))


def rel_sorted(iterable, *quotas, key=None, reverse=False):
    """
    Exactly like sorted(), except the sorting is relative to how close the
    usage is to a quota. The key specifies a function of one argument that is
    used to extract the usage corresponding to the given quotas (in the same
    order).

    >>> get_quotas = lambda u: u.cpu_usage, u.mem_usage
    >>> rel_sorted(users, cpu_quota, mem_quota, key=get_quotas)
    [user, ..., ...]
    """
    rel_usage_key = lambda i: _rel_usage(i, *quotas, key=key)
    return sorted(
        iterable,
        key=rel_usage_key,
        reverse=reverse
    )


def _rel_usage(item, *quotas, key=None):
    iter_quotas = iter(quotas)
    usages = key(item) if key else item
    try:
        iter(usages)
    except TypeError:
        usages = [usages]  # Just in case only one usage is provided
    return sum(usage / next(iter_quotas) for usage in usages)
