"""
A module that contains a base class and methods related to storing usage
information. This module is meant to be used for creating unique objects that
can be used to collect, store and process information. These objects follow the
following philosophy:

The collection of usage metrics is done by creating a Instance(), which stores
instaneous values upon creation. This Instance() should then be able to be
divided by another Instance() (by overriding __truediv__) to create a Static()
object, which takes the two instaneous values and merges them together into
human readable usage values. This Static() object should be able to do
appropriate arithmetic with another Static() object (partially using the
Usage() arithmatic). In order to facilitate later use of a object (e.g. getting
the uptime of a process, or setting quotas of a cgroup), Instance() objects
should inherit from a object that contains methods and properties to facilitate
this. This object should inherit from Usage(). Thus, the inheritance should
follow this pattern (left to right):

Usage() -> Static_Facilitator_() -> _Facilitator_() -> _Facilitator_Instance()

The _Facilitator_() should be named appropriately (e.g. Process()).

Other Notes:
Instance() objects may/should throw exceptions if information cannot be
collected in __init__. The _Facilitator_() should only do this if necessary.
"""

metrics = {"cpu": 0, "mem": 0}


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
            kwargs = vars(self)
            kwargs["usage"] = {
                metric: usage + other.usage[metric]
                for metric, usage in self.usage.items()
            }
            return type(self)(**kwargs)
        else:
            kwargs = vars(self)
            kwargs["usage"] = {
                metric: usage + other
                for metric, usage in self.usage.items()
            }
            return type(self)(**kwargs)

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        if isinstance(other, type(self)):
            kwargs = vars(self)
            kwargs["usage"] = {
                metric: usage - other.usage[metric]
                for metric, usage in self.usage.items()
            }
            return type(self)(**kwargs)
        else:
            kwargs = vars(self)
            kwargs["usage"] = {
                metric: usage - other
                for metric, usage in self.usage.items()
            }
            return type(self)(**kwargs)

    def __rsub__(self, other):
        return self.__sub__(other)

    def __truediv__(self, other):
        kwargs = vars(self)
        kwargs["usage"] = {
            metric: usage / other for metric, usage in self.usage.items()
        }
        return type(self)(**kwargs)

    def __floordiv__(self, other):
        kwargs = vars(self)
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
