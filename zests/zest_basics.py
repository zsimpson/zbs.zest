"""
This is a (self-referential) test that tests zest itself.
It also serves as an example of how to build a zest.
"""

from zest import zest


def zest_basics():

    def it_calls_before():
        test_count = 0
        before_count = 0

        def _before():
            nonlocal before_count
            before_count += 1

        def test1():
            nonlocal test_count
            test_count += 1

        def test2():
            nonlocal test_count
            test_count += 1

        zest()

        assert test_count == 2 and before_count == 2

    # def it_calls_after():
    #     raise NotImplementedError
    #
#     def it_raises_on_begin():
#         raise NotImplementedError
#
#     def it_shuffles_by_default():
#         raise NotImplementedError
#
#     def it_can_disable_shuffle():
#         raise NotImplementedError
#
#     def it_can_limit_tests():
#         raise NotImplementedError
#
#     def it_calls_start_callback():
#         raise NotImplementedError
#
#     def it_calls_stop_callback():
#         raise NotImplementedError
#
#     def it_warns_if_no_trailing_zest():
#         raise NotImplementedError
#
    zest()

# def zest_raises():
#
#     def it_catches_raises():
#         raise NotImplementedError
#
#     def it_checks_properties_of_exception():
#         raise NotImplementedError
#
# def zest_groups():
#     def it_marks_groups():
#         raise NotImplementedError
#
#     def it_skips():
#         raise NotImplementedError
#
#     zest()
#
# def zest_mocks():
#     def it_scope_mocks():
#         raise NotImplementedError
#
#     def it_stack_mocks():
#         raise NotImplementedError
#
#         def it_resets_after_each_test():
#             raise NotImplementedError
#
#         zest()
#
#     def it_counts_n_calls():
#         raise NotImplementedError
#
#     def it_resets():
#         raise NotImplementedError
#
#     def it_hooks():
#         raise NotImplementedError
#
#     def it_returns_value():
#         raise NotImplementedError
#
#     def it_returns_serial_values():
#         raise NotImplementedError
#
#     def it_exceptions():
#         raise NotImplementedError
#
#     def it_exceptions_serially():
#         raise NotImplementedError
#
#     def it_normalizes_calls_into_kwargs():
#         raise NotImplementedError
#
#     def it_can_check_a_single_call_with_args_and_kwargs():
#         raise NotImplementedError
#
#     zest()
