"""
This is a (self-referential) test that tests zest itself.
It also serves as an example of how to build a zest.
"""

from zest import zest, TrappedException
from zest_runner import ZestRunner
from . import pretend_unit_under_test
from .pretend_unit_under_test import foo
from version import __version__
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


def zest_groups():
    # TODO: Needs to be tested in a zest_runner test
    # def it_marks_groups():
    #     raise NotImplementedError

    @zest.skip("s", "not done")
    def it_skips():
        # If this actually ran an exception would get raised
        raise NotImplementedError

    zest()


def zest_mocks():
    def scope_mocks():
        @zest.skip("!", "broken")
        def it_raises_on_incorrect_local_import():
            # Mocked symbols should not be directly imported into the
            # zest file but rather the module under test should be imported
            with zest.raises(AssertionError, in_args="module-level symbol"):
                with zest.mock(foo) as m_foo:
                    foo()

        def it_mocks_an_external_symbol():
            with zest.mock(pretend_unit_under_test.foo) as m_foo:
                pretend_unit_under_test.foo()
                # Had the real foo been called it would have
                # raised NotImplementedError

            assert m_foo.called_once()

        zest()

    def stack_mocks():
        # stack_mocks, unlike scope mocks, are reset before each test

        @zest.skip("!", "broken")
        def it_raises_on_incorrect_local_import():
            with zest.raises(AssertionError, in_args="module-level symbol"):
                with zest.mock(foo) as m_foo:
                    foo()

        @zest.skip("!", "broken")
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

    # TODO

    # def it_normalizes_calls_into_kwargs():
    #     with zest.mock(pretend_unit_under_test.foo) as m_foo:
    #         pretend_unit_under_test.foo("arg1")

    #     def it_can_check_a_single_call_with_args_and_kwargs():
    #         raise NotImplementedError
    #
    zest()


def call_zest(*args):
    # Run zest_runner in a sub-processes so that we don't end up with
    # recursion problems since these tests themselves is running under ZestRunner
    try:
        output = subprocess.check_output(
            "python ./zest/zest_runner.py " + " ".join([f'"{a}"' for a in args]),
            shell=True,
            stderr=subprocess.STDOUT,
        )
        ret_code = 0
    except subprocess.CalledProcessError as e:
        ret_code = e.returncode
        output = e.output
    return ret_code, output.decode("utf-8")


def zest_runner():
    def it_returns_version():
        ret_code, output = call_zest("--version")
        assert ret_code == 0 and output.strip() == __version__

    def it_shuffles_by_default():
        ret_code, output = call_zest("--verbose=2", "--disable_shuffle", "zest_basics")
        print("GOT", ret_code, output)

        # runner = ZestRunner(
        #     verbose=1,
        #     include_dirs="./zests",
        #     match_string=None,
        #     recurse=0,
        #     groups=None,
        #     disable_shuffle=False,
        # )

    zest()


#     def it_can_disable_shuffle():
#         raise NotImplementedError
#
#     def it_can_limit_tests():
#         raise NotImplementedError
#
#     def it_warns_if_no_trailing_zest():
#         raise NotImplementedError
#
