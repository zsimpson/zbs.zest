"""
This is a (self-referential) test that tests zest itself.
It also serves as an example of how to build a zest.
"""

import re
from zest import zest, TrappedException
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
        def _test_start_callback(name, call_stack, func):
            nonlocal start_was_called
            start_was_called += 1

        def _test_stop_callback(name, call_stack, error, elapsed, func):
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

    def it_raises_on_greater_than_one_char_skip_code():
        with zest.raises(ValueError, in_args="only be one character"):
            @zest.skip("toolong")
            def it_raises_on_too_long():
                pass

    zest()


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


@zest.skip(reason="it can handle keyword skips")
def zest_it_can_handle_keyword_skips():
    pass


@zest.skip("s")
def zest_it_can_skip_with_a_chracter_mark():
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

            def callback():
                nonlocal got_callback
                got_callback = True

            m_foo.hook(callback)
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


@zest.skip(reason="bad_zests")
def zest_bad_zests():
    # These are special cases that are bad which are excluded
    # except in the zest_runner case below that tests that the
    # errors are correctly detected
    def it_foobars():
        pass

    # NOTE, this does not call zest() as it should!


def _call_zest(*args):
    # Run zest_runner in a sub-processes so that we don't end up with
    # recursion problems since these tests themselves is running under ZestRunner
    try:
        to_run = "python -m zest.zest_runner " + " ".join(args)
        # print(f"TO RUN: {to_run}")
        output = subprocess.check_output(
            to_run,
            shell=True,
            stderr=subprocess.STDOUT,
        )
        ret_code = 0
    except subprocess.CalledProcessError as e:
        ret_code = e.returncode
        output = e.output
    return ret_code, output.decode("utf-8")


@zest.group("zest_runner")
def zest_runner():
    def _get_run_tests(output):
        found_tests = []
        for line in output.split("\n"):
            m = re.search(r"^\s*([a-z0-9_]+)", line)
            if m:
                skipped = re.search(r"skipped", line, re.IGNORECASE)
                if not skipped:
                    found_tests += [m.group(1)]
        return found_tests

    def it_returns_version():
        ret_code, output = _call_zest("--version")
        assert ret_code == 0 and output.strip() == __version__

    def shuffling():
        def _all_identical_ordering(disable_shuffle):
            first_found_tests = []
            for tries in range(5):
                ret_code, output = _call_zest(
                    "--verbose=2",
                    "--disable_shuffle" if disable_shuffle else "",
                    "zest_basics",
                )
                if ret_code != 0:
                    print(output)
                assert ret_code == 0

                found_tests = _get_run_tests(output)
                if len(first_found_tests) == 0:
                    first_found_tests = list(found_tests)
                else:
                    if found_tests != first_found_tests:
                        return False
            else:
                return True

        def it_shuffles_by_default():
            assert not _all_identical_ordering(False)

        def it_can_disable_shuffle():
            assert _all_identical_ordering(True)

        zest()

    def it_runs_parent_tests():
        ret_code, output = _call_zest("--verbose=2", "level_two")
        found_tests = _get_run_tests(output)
        assert found_tests == ["zest_basics", "it_recurses", "level_one", "level_two"]

    def it_warns_if_no_trailing_zest():
        ret_code, output = _call_zest(
            "--verbose=2", "--bypass_skip=bad_zests", "zest_bad_zests"
        )
        assert "did not terminate with a call to zest" in output
        assert ret_code != 0

    def runs_groups():
        n_expected_tests = 34
        # I don't like this hard coded run count but I don't know a better way at moment

        def it_runs_all_tests_by_default():
            # To prevent recursion, add skip the zest_runner group
            ret_code, output = _call_zest("--skip_groups=zest_runner", "--verbose=2")
            assert ret_code == 0
            ran = _get_run_tests(output)
            assert "zest_a_named_group" in ran
            assert len(ran) == n_expected_tests + 1  # +1 because zest_a_named_group

        def it_can_limit_to_one_group():
            ret_code, output = _call_zest("--verbose=2", "--run_groups=a_named_group", "--skip_groups=zest_runner")
            assert ret_code == 0
            ran = _get_run_tests(output)
            assert ran == ["zest_a_named_group"]

        def it_runs_unmarked_tests_under_name_unit():
            ret_code, output = _call_zest("--verbose=2", "--run_groups=unit", "--skip_groups=zest_runner")
            assert ret_code == 0
            ran = _get_run_tests(output)
            assert len(ran) == n_expected_tests
            assert "zest_a_named_group" not in ran

        zest()

    zest()
