from zest import zest


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

    def it_fails_1():
        #raise Exception()
        pass

    def it_fails_2():
        #raise Exception()
        pass

    def it_fails_3():
        #raise Exception()
        pass

    zest()