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


class ZestRunnerSingleThread:

    def _display_start(self, name, last_depth, curr_depth, add_markers):
        if last_depth < curr_depth:
            s("\n")
        marker = "+" if add_markers else ""
        s("  " * curr_depth, yellow, marker + name, reset, ": ")
        # Note, no \n on this line because it will be added on the display_stop call


    def _display_stop(self, error, elapsed, skip, last_depth, curr_depth):
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


    def _display_abbreviated(self, error, skip):
        """Overload this to change output behavior"""
        if error:
            s(bold, red, "F")
        elif skip:
            s(yellow, "s")
        else:
            s(green, ".")


    def _display_warnings(self, call_warnings):
        for warn in call_warnings:
            s(yellow, warn, "\n")

    def __init__(
        self,
        root=None,
        include_dirs=None,
        allow_to_run="__all__",
        allow_files=None,
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
        allow_files:
            If not None: A colon-delimited list of filenames (without paths or extension) that will be allowed
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
        self.include_dirs = include_dirs
        self.allow_to_run = allow_to_run
        self.allow_files = allow_files
        self.match_string = match_string
        self.exclude_string = exclude_string
        self.verbose = verbose
        self.add_markers = add_markers
        self.bypass_skip = bypass_skip

        zest.reset()
        zest._disable_shuffle = disable_shuffle
        zest._bypass_skip = bypass_skip.split(":") if bypass_skip is not None else []
        self.n_zest_missing_errors = 0
        self.results = []

        # zest runner must start in the root of the project
        # so that modules may be loaded appropriately.
        self.root = root or os.getcwd()
        assert self.root[0] == os.sep

    def run(self):
        last_depth = 0
        curr_depth = 0

        root_zests, allow_to_run, find_errors = zest_finder.find_zests(
            self.root,
            self.include_dirs,
            self.allow_to_run.split(":"),
            self.allow_files.split(":") if self.allow_files is not None else None,
            self.match_string,
            self.exclude_string,
            self.bypass_skip,
        )

        # TODO: Follow same ZestRunnerErrors pattern established in multi...
        if len(find_errors) > 0:
            display_errors(find_errors)
            return 1

        # Event functions are callbacks from zest
        # ---------------------------------------------------------------------------------
        def event_test_start(zest_result):
            """Track the callback depth and forward to the display_start()"""
            nonlocal last_depth, curr_depth
            if self.verbose >= 2:
                curr_depth = len(zest_result.call_stack) - 1
                self._display_start(zest_result.short_name, last_depth, curr_depth, self.add_markers)
                last_depth = curr_depth

        def event_test_stop(zest_result):
            """
            Track the callback depth and forward to display_stop() or display_abbreviated()
            """
            nonlocal last_depth, curr_depth
            self.results += [zest_result]
            curr_depth = len(zest_result.call_stack) - 1
            if self.verbose >= 2:
                self._display_stop(
                    zest_result.error,
                    zest_result.elapsed,
                    zest_result.skip,
                    last_depth,
                    curr_depth,
                )
            elif self.verbose == 1:
                self._display_abbreviated(zest_result.error, zest_result.skip)

        def event_complete():
            if self.verbose > 0:
                display_complete(self.root, self.results)

            if self.verbose > 1:
                s("Slowest 5%\n")
                n_timings = len(self.results)
                timings = [
                    (result.full_name, result.elapsed) for result in self.results
                ]
                timings.sort(key=lambda tup: tup[1])
                ninety_percentile = 95 * n_timings // 100
                for i in range(n_timings - 1, ninety_percentile, -1):
                    name = timings[i]
                    s("  ", name[0], gray, f" {int(1000.0 * name[1])} ms)\n")

            if self.verbose > 0:
                self._display_warnings(zest._call_warnings)

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
        retcode = 0 if len(zest._call_errors) == 0 else 1

        return retcode
