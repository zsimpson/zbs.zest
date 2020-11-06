"""
Single-threaded runner with abbreviated and verbose display options
"""

import sys
import os
import re
from zest import zest
from zest.zest import log
from zest import zest_finder
from zest.zest_display import *


# Display functions output messages
# ---------------------------------------------------------------------------------


def _display_start(name, last_depth, curr_depth, add_markers):
    if last_depth < curr_depth:
        s("\n")
    marker = "+" if add_markers else ""
    s("  " * curr_depth, yellow, marker + name, reset, ": ")
    # Note, no \n on this line because it will be added on the display_stop call


def _display_stop(error, elapsed, skip, last_depth, curr_depth):
    if curr_depth < last_depth:
        s(f"{'  ' * curr_depth}")
    if isinstance(error, str) and error.startswith("skipped"):
        s(bold, yellow, error)
    elif skip is not None:
        s(bold, yellow, "SKIPPED (reason: ", skip, ")")
    elif error:
        s(bold, red, "ERROR", gray, f" (in {int(1000.0 * elapsed)} ms)")
    else:
        s(green, "SUCCESS", gray, f" (in {int(1000.0 * elapsed)} ms)")
    s("\n")


def _display_abbreviated(error, skip):
    """Overload this to change output behavior"""
    if error:
        s(bold, red, "F")
    elif skip:
        s(yellow, "s")
    else:
        s(green, ".")


def _display_warnings(call_warnings):
    for warn in call_warnings:
        s(yellow, warn, "\n")


# Entrypoint
# ---------------------------------------------------------------------------------


def run_zests(
    root=None,
    include_dirs=None,
    allow_to_run="__all__",
    match_string=None,
    exclude_string=None,
    verbose=1,
    disable_shuffle=False,
    add_markers=False,
    bypass_skip=None,
    **kwargs,
):
    """
    root:
        The directory under which should be searched for zests and outside of which
        will be considered "library references" (greayed out in error messages)
    include_dirs:
        The folders (relative to root) that should be included in recursive search
    allow_to_run:
        If not None: A colon-delimited list of full test names (dot-delimited) that will be allowed to run
        Special values:
            __all__: Consider all zests to run
            __failed__: Consider previous failed zests
    match_string:
        If not None: A substring that if found in a zest name will include it
        Note: If allow_to_run includes only a subset of zests then this match_string
        can only further restrict the set. A match_string of None does not further restrict
        the list at all.
    exclude_string:
        If not None: A substring that if found in a zest name will exclude it
    verbose:
        0: no output
        1: normal output (dots notation)
        2: full test output (with names)
        3: debugging traces
    disable_shuffle:
        True: runs zests in consistent order.
        False (default): shuffles zests to root out order dependencies
    capture:
        If True, capture all stdio
    add_markers:
        Used for debugging. Ignore.
    bypass_skip:
        Used for debugging. Ignore.
    """
    zest.reset()
    zest._disable_shuffle = disable_shuffle
    zest._bypass_skip = bypass_skip.split(":") if bypass_skip is not None else []
    n_zest_missing_errors = 0
    last_depth = 0
    curr_depth = 0
    results = {}

    # zest runner must start in the root of the project
    # so that modules may be loaded appropriately.
    root = root or os.getcwd()
    assert root[0] == os.sep

    root_zests, allow_to_run, errors = zest_finder.find_zests(
        root,
        include_dirs,
        allow_to_run.split(":"),
        match_string,
        exclude_string,
        bypass_skip,
    )

    # TODO: Follow same ZestRunnerErrors pattern established in multi...
    if len(errors) > 0:
        display_errors(errors)
        return 1

    # Event functions are callbacks from zest
    # ---------------------------------------------------------------------------------
    def event_test_start(zest_result):
        """Track the callback depth and forward to the display_start()"""
        nonlocal last_depth, curr_depth
        if verbose >= 2:
            curr_depth = len(zest_result.call_stack) - 1
            _display_start(zest_result.short_name, last_depth, curr_depth, add_markers)
            last_depth = curr_depth

    def event_test_stop(zest_result):
        """
        Track the callback depth and forward to display_stop() or display_abbreviated()
        """
        nonlocal last_depth, curr_depth
        results[zest_result.full_name] = zest_result
        curr_depth = len(zest_result.call_stack) - 1
        if verbose >= 2:
            _display_stop(
                zest_result.error,
                zest_result.elapsed,
                zest_result.skip,
                last_depth,
                curr_depth,
            )
        elif verbose == 1:
            _display_abbreviated(zest_result.error, zest_result.skip)

    def event_complete():
        if verbose > 0:
            display_complete(root, zest._call_log, zest._call_errors)

        if verbose > 1:
            s("Slowest 5%\n")
            n_timings = len(results)
            timings = [
                (full_name, result.elapsed) for full_name, result in results.items()
            ]
            timings.sort(key=lambda tup: tup[1])
            ninety_percentile = 95 * n_timings // 100
            for i in range(n_timings - 1, ninety_percentile, -1):
                name = timings[i]
                s("  ", name[0], gray, f" {int(1000.0 * name[1])} ms)\n")

        if verbose > 0:
            _display_warnings(zest._call_warnings)

    # LAUNCH root zests
    for (root_name, (module_name, package, full_path)) in root_zests.items():
        root_zest_func = zest_finder.load_module(root_name, module_name, full_path)
        zest.do(
            root_zest_func,
            test_start_callback=event_test_start,
            test_stop_callback=event_test_stop,
            allow_to_run=allow_to_run,
        )

    # Event functions are callbacks from zest
    # ---------------------------------------------------------------------------------

    event_complete()
    retcode = 0 if len(zest._call_errors) == 0 and n_zest_missing_errors == 0 else 1

    return retcode
