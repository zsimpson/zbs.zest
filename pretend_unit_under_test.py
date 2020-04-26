"""
This is a pretend unit under test. See zests_examples.py
"""

not_callable = 1


def foo(arg1, arg2=None):
    raise NotImplementedError


def foobar():
    foo(1)


class FooClass:
    @staticmethod
    def static_meth():
        raise NotImplementedError

    @classmethod
    def class_meth(cls):
        raise NotImplementedError

    def meth(self):
        raise NotImplementedError
