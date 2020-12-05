"""
This is an advanced test that calls zest command line tools
to test single and multi-runner models.
"""

import time
import re
import os
from contextlib import contextmanager
from zest import zest, TrappedException
from zest.zest import log, strip_ansi
import pretend_unit_under_test
from zest.version import __version__
import subprocess


#@zest.parameter_list([1, 2])
@zest.parameter_list([2])
def zest_runner(n_workers):
    """Test all options under single and multi threaded models"""

    def _call_zest_cli(*args):
        """
        Run zest_runner in a sub-processes to avoid recursion issues
        (these tests are themselves running under ZestRunner).
        Plus, captures output for analysis.
        """
        try:
            to_run = f"python -m zest.zest_cli --add_markers --allow_files=zest_basics --n_workers={n_workers} " + " ".join(args)
            output = subprocess.check_output(to_run, shell=True, stderr=subprocess.STDOUT,)
            ret_code = 0
        except subprocess.CalledProcessError as e:
            ret_code = e.returncode
            output = e.output
        return ret_code, output.decode("utf-8")

    def _get_run_tests(output):
        found_tests = []
        for line in output.split("\n"):
            m = re.search(r"^[^\+]*[\+]([a-z0-9_]+)", line)
            if m:
                skipped = re.search(r"skipped", line, re.IGNORECASE)
                if not skipped:
                    found_tests += [m.group(1)]
        return found_tests

    def it_returns_version():
        ret_code, output = _call_zest_cli("--version")
        assert ret_code == 0 and output.strip() == __version__

    def shuffling():
        def _all_identical_ordering(disable_shuffle):
            first_found_tests = []
            for tries in range(5):
                ret_code, output = _call_zest_cli(
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
        ret_code, output = _call_zest_cli("--verbose=2", "level_two")
        found_tests = _get_run_tests(output)
        assert found_tests == ["zest_basics", "it_recurses", "level_one", "level_two"]

    def it_skips():
        ret_code, output = _call_zest_cli("--verbose=2", "zest_bad_zest_1")
        assert "+zest_bad_zest_1: SKIPPED" in strip_ansi(output)

    def it_skips_bypass():
        ret_code, output = _call_zest_cli(
            "--verbose=2", "zest_bad_zest_1", "--bypass_skip=bad_zest_1"
        )
        assert "+zest_bad_zest_1: SKIPPED" not in strip_ansi(output)

    def it_warns_if_no_trailing_zest():
        ret_code, output = _call_zest_cli(
            "--verbose=2", "--bypass_skip=bad_zest_1", "zest_bad_zest_1"
        )
        assert "did not terminate with a call to zest" in output
        assert "zest_basics.py:" in output
        assert ret_code != 0

    def it_warns_if_zest_not_final():
        ret_code, output = _call_zest_cli(
            "--verbose=2", "--bypass_skip=bad_zest_2", "zest_bad_zest_2"
        )
        assert "before all functions were defined" in output
        assert "zest_basics.py:" in output
        assert ret_code != 0

    zest()


def _call_multi_zest(*args):
    return _call_zest_cli(*(args + ("--n_workers=2",)))


"""
def zest_runner_multi_thread():
    def _get_run_tests(output):
        found_tests = []
        for line in output.split("\n"):
            m = re.search(r"^[^\+]*[\+]([a-z0-9_]+)", line)
            if m:
                skipped = re.search(r"skipped", line, re.IGNORECASE)
                if not skipped:
                    found_tests += [m.group(1)]
        return found_tests

    def it_returns_version():
        ret_code, output = _call_multi_zest("--version")
        assert ret_code == 0 and output.strip() == __version__

    def it_skips():
        ret_code, output = _call_multi_zest()
        log(f"OUTPUT {output}")
        assert "noisy_zests" not in output
        assert ret_code == 0 and output.strip() == __version__

    def it_runs_parent_tests():
        ret_code, output = _call_multi_zest("--verbose=2", "level_two")
        found_tests = _get_run_tests(output)
        assert found_tests == ["zest_basics", "it_recurses", "level_one", "level_two"]

    def it_warns_if_no_trailing_zest():
        ret_code, output = _call_multi_zest(
            "--verbose=2", "--bypass_skip=bad_zest_1", "zest_bad_zest_1"
        )
        assert "did not terminate with a call to zest" in output
        assert "zest_examples.py:" in output
        assert ret_code != 0

    def it_warns_if_zest_not_final():
        ret_code, output = _call_multi_zest(
            "--verbose=2", "--bypass_skip=bad_zest_2", "zest_bad_zest_2"
        )
        assert "before all functions were defined" in output
        assert "zest_examples.py:" in output
        assert ret_code != 0

    zest()
"""
