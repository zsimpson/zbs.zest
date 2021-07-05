"""
This is a simple test that tests the basics of zest itself.
It also serves as an example of how to build a zest.

It does not include any complicated self-referential tests,
see
"""

import time
import re
import os
import sys
from contextlib import contextmanager
from zest import zest, TrappedException
from zest.zest import log, strip_ansi
import pretend_unit_under_test
from zest.version import __version__
import subprocess


def zest_basics():
    def it_calls_before_and_after():
        test_count = 0
        before_count = 0
        after_count = 0

        def _before():
            nonlocal before_count
            before_count += 1

        def _after():
            nonlocal after_count
            after_count += 1

        def test1():
            nonlocal test_count
            test_count += 1

        def test2():
            nonlocal test_count
            test_count += 1

        zest()

        assert test_count == 2 and before_count == 2 and after_count == 2

    def it_raises_on_begin():
        # _begin is easily confused with "_before" so there's a special check for it
        with zest.raises(ValueError, in_args="_before"):

            def _begin():
                # Should have been "_before"
                pass

            def test1():
                pass

            zest()

    def it_ignores_underscored_functions():
        test_count = 0
        ignored_count = 0

        def _ignored():
            nonlocal ignored_count
            ignored_count += 1

        def real_test():
            nonlocal test_count
            test_count += 1

        zest()

        assert test_count == 1 and ignored_count == 0

    def it_calls_start_and_stop_callbacks():
        start_was_called = 0
        stop_was_called = 0

        # Note the following two callbacks are ignored because they are underscored
        def _test_start_callback(zest_result):
            nonlocal start_was_called
            start_was_called += 1

        def _test_stop_callback(zest_result):
            nonlocal stop_was_called
            stop_was_called += 1

        def test1():
            pass

        def test2():
            pass

        zest(
            test_start_callback=_test_start_callback,
            test_stop_callback=_test_stop_callback,
        )

        assert start_was_called == 2 and stop_was_called == 2

    def it_recurses():
        def level_one():
            def level_two():
                pass

            zest()

        zest()

    zest()


def zest_retries():
    t = 0

    @zest.retry(2)
    def fails_on_first_try():
        nonlocal t
        t += 1
        if t == 1:
            raise Exception("Fails on first try")

    zest()

@contextmanager
def some_context():
    yield


def zest_runs_inside_context():
    found_func_inside_context = False

    with some_context():

        def it_finds_this_func():
            nonlocal found_func_inside_context
            found_func_inside_context = True

        zest()

    assert found_func_inside_context


def zest_raises():
    def it_catches_raises():
        with zest.raises(ValueError) as e:
            raise ValueError("test")
        assert isinstance(e, TrappedException) and isinstance(e.exception, ValueError)

    def it_checks_properties_of_exception():
        class MyException(Exception):
            def __init__(self, foo):
                self.foo = foo

        def it_passes_if_property_found():
            with zest.raises(MyException, in_foo="bar") as e:
                raise MyException(foo="bar")

        def it_fails_if_property_not_found():
            # Tricky test -- using "with zest.raises()" to catch the
            # AssertionError that is raised when the inner MyException
            # does not contain the expected property
            with zest.raises(AssertionError) as outer_e:
                with zest.raises(MyException, in_foo="blah") as e:
                    raise MyException(foo="bar")
                assert isinstance(e, TrappedException) and isinstance(
                    e.exception, MyException
                )
            assert "exception to have" in str(outer_e.exception)

        zest()

    def it_checks_args_of_exception():
        with zest.raises(ValueError, in_args="bar"):
            raise ValueError("not", "bar")

    zest()


@zest.group("a_named_group")
def zest_a_named_group():
    pass


@zest.skip("it can handle keyword skips")
def zest_it_can_handle_keyword_skips():
    pass

def zest_fails_1():
    raise Exception("FAIL")
    pass

def zest_fails_2():
    raise Exception("FAIL")
    pass


def zest_mocks():
    def scope_mocks():
        with zest.mock(pretend_unit_under_test.foo) as m_foo:
            pretend_unit_under_test.foo()
            # Had the real foo been called it would have
            # raised NotImplementedError

        assert m_foo.called_once()

    def stack_mocks():
        # stack_mocks, unlike scope mocks, will reset before each test

        def it_mocks_an_external_symbol_with_resets():
            m_foo = zest.stack_mock(pretend_unit_under_test.foo)

            def test_0():
                pretend_unit_under_test.foo()
                assert m_foo.called_once()

            def test_1():
                pretend_unit_under_test.foo()
                assert m_foo.called_once()

            zest()

        zest()

    def it_raises_if_mock_is_not_callable():
        with zest.raises(AssertionError, in_args="Unmockable"):
            with zest.mock(pretend_unit_under_test.not_callable):
                pass

    def it_counts_n_calls():
        with zest.mock(pretend_unit_under_test.foo) as m_foo:
            pretend_unit_under_test.foo()
            pretend_unit_under_test.foo()
        assert m_foo.n_calls == 2

    def it_resets():
        with zest.mock(pretend_unit_under_test.foo) as m_foo:
            pretend_unit_under_test.foo()
            m_foo.reset()
            pretend_unit_under_test.foo()
        assert m_foo.n_calls == 1

    def it_hooks():
        with zest.mock(pretend_unit_under_test.foo) as m_foo:
            got_callback = False

            def _callback():
                nonlocal got_callback
                got_callback = True

            m_foo.hook(_callback)
            pretend_unit_under_test.foo()
            assert got_callback is True

    def it_returns_value():
        with zest.mock(pretend_unit_under_test.foo) as m_foo:
            m_foo.returns(1)
            assert pretend_unit_under_test.foo() == 1

    def it_returns_serial_values():
        with zest.mock(pretend_unit_under_test.foo) as m_foo:
            m_foo.returns_serially([1, 2])
            assert pretend_unit_under_test.foo() == 1
            assert pretend_unit_under_test.foo() == 2

    def it_exceptions():
        with zest.mock(pretend_unit_under_test.foo) as m_foo:
            m_foo.exceptions(ValueError)
            with zest.raises(ValueError):
                pretend_unit_under_test.foo()

    def it_exceptions_serially():
        with zest.mock(pretend_unit_under_test.foo) as m_foo:
            m_foo.exceptions_serially([ValueError, TypeError])
            with zest.raises(ValueError):
                pretend_unit_under_test.foo()
            with zest.raises(TypeError):
                pretend_unit_under_test.foo()

    def it_normalizes_calls_into_kwargs():
        # normalized_call() is a handy when you want to just know
        # what was passed to the mock but you don't care if
        # it was passed as args or kwargs.

        with zest.mock(pretend_unit_under_test.foo) as m_foo:
            pretend_unit_under_test.foo("arg1", arg2="arg2")

        kwargs = m_foo.normalized_call()
        assert kwargs == dict(arg1="arg1", arg2="arg2")

    def it_checks_against_normalized_call():
        with zest.mock(pretend_unit_under_test.foo) as m_foo:
            pretend_unit_under_test.foo("arg1", arg2="arg2")

        assert m_foo.called_once_with_kws(arg1="arg1", arg2="arg2")

    zest()


@zest.skip(reason="bad_zest_1")
def zest_bad_zest_1():
    """
    This is a malformed test that is expected to issue a warning
    when it is tested by the below it_warns_if_no_trailing_zest
    """

    def it_foobars():
        pass

    def outer_foobar():
        def inner_foobar():
            pass

        # Inner does call zest
        zest()

    # Outer does not call zest


@zest.skip(reason="bad_zest_2")
def zest_bad_zest_2():
    """
    Like zest_bad_zest_1 but with an error of a zest() before final test.
    """

    def it_foobars():
        pass

    # zest before final
    zest()

    def outer_foobar():
        pass


@zest.skip(reason="noisy_zests")
def zest_noisy_zests():
    """
    Emits to stdout and stderr to test capturing.
    """

    def it_foobars():
        print("This is to stdout")
        print("This is to stderr", file=sys.stderr)

    zest()


@zest.group("group1")
def zest_group1():
    def it_foos():
        pass

    zest()


@zest.group("group2")
def zest_group2():
    def it_foos():
        pass

    zest()


def zest_slow():
    def it_runs_slowly():
        pass

    zest()


@zest.skip(reason="no_call_to_before_on_skips")
def zest_no_call_to_before_on_skips():
    def _before():
        raise Exception("Should not happen")

    def do_not_run_this():
        # This test will be excluded by a non-match and therefore _before should not be called
        pass

    zest()


@zest.skip(reason="tmp_folder_per_test")
def zest_tmp_folder_per_test():
    def test1():
        print("test1", os.getcwd())

    def test2():
        print("test2", os.getcwd())

    zest()


@zest.skip(reason="captures")
def zest_captures():
    def it_captures_stdout():
        print("To stdout")

    def it_captures_stderr():
        print("To stderr", file=sys.stderr)

    zest()


"""
def zest_parameter_list():
    saw = {1: False, 2: False}

    @zest.parameter_list([1, 2])
    def it_calls_multiple_times(arg):
        nonlocal saw
        saw[arg] = True

        def it_has_an_interior_method():
            pass

        zest()

    zest()

    assert saw[1] is True and saw[2] is True
    assert zest._call_log == [
        "zest_parameter_list()",
        "zest_parameter_list.it_calls_multiple_times(1,)",
        "zest_parameter_list.it_calls_multiple_times(2,)",
    ]

    [
        'zest_parameter_list()',
        'zest_parameter_list().it_calls_multiple_times(1,)',
        'zest_parameter_list().it_calls_multiple_times(1,).it_has_an_interior_method()',
        'zest_parameter_list().it_calls_multiple_times(1, ).it_has_an_interior_method().it_calls_multiple_times(2, )',
        'zest_parameter_list().it_calls_multiple_times(1, ).it_has_an_interior_method().it_calls_multiple_times(2, ).it_has_an_interior_method()'
    ]
"""
