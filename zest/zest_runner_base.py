import glob
import time
import json
import os
import re
import io
import random
import sys
import signal
import multiprocessing
import traceback
import pathlib
from contextlib import contextmanager
from zest.zest import ZestResult
from multiprocessing import Queue
from queue import Empty
from collections import deque
from pathlib import Path
from zest import zest
from zest.zest import log
from zest import zest_finder
from zest import zest_display


@contextmanager
def open_event_stream(output_folder, root_name):
    f = None
    try:
        f = open(f"{output_folder}/{root_name}.evt", "a+b", buffering=0)
        yield f
    finally:
        if f is not None:
            f.close()


def emit_zest_result(zest_result, stream):
    assert isinstance(zest_result, ZestResult)
    msg = (zest_result.dumps() + "\n").encode()
    stream.write(msg)
    stream.flush()


class ZestRunnerBase:
    def __init__(
        self,
        output_folder=Path(".zest_results"),
        callback=None,
        root=None,
        include_dirs=None,
        allow_to_run="__all__",
        allow_files=None,
        match_string=None,
        exclude_string=None,
        bypass_skip=None,
        capture=False,
        verbose=1,
        disable_shuffle=False,
        add_markers=False,
        groups=None,
        exclude_groups=None,
        common_tmp=None,
        tmp_root="/tmp",
        preview=False,
        **kwargs,
    ):
        """
        output_folder:
            The directory where results will be written
        callback:
            If not None, callback on each zest
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
        bypass_skip:
            Used for debugging. Ignore.
        capture:
            If True, capture all stdio
        verbose:
            0: no output
            1: normal output (dots notation)
            2: full test output (with names)
            3: debugging traces
        disable_shuffle:
            True: runs zests in consistent order.
            False (default): shuffles zests to root out order dependencies
        add_markers:
            Used for debugging. Ignore.
        common_tmp:
            If non-None, use this one folder as the CWD for all tests.
            Otherwise a tmp folder will be created for each test
            (and removed if the test passes)
        tmp_root: (default "/tmp")
            The root of any auto-generated tmp files

        """
        zest._bubble_exceptions = False

        self.callback = callback
        self.output_folder = pathlib.Path(output_folder)
        self.n_run = 0
        self.capture = capture
        self.results = []
        self.retcode = 0
        self.verbose = verbose
        self.add_markers = add_markers
        self.allow_to_run = allow_to_run
        self.output_folder.mkdir(parents=True, exist_ok=True)
        self.disable_shuffle = disable_shuffle
        self.bypass_skip = bypass_skip
        self.groups = groups
        self.exclude_groups = exclude_groups
        self.common_tmp = common_tmp
        self.tmp_root = tmp_root

        zest.reset(disable_shuffle, bypass_skip, common_tmp, tmp_root, capture)

        # zest runner must start in the root of the project
        # so that modules may be loaded appropriately.
        self.root = root or os.getcwd()
        assert self.root[0] == os.sep

        allow_list = self.allow_to_run.split(":")

        for r in allow_list:
            if r == "__failed__":
                # load_previous is a bit slow so we only want to do it if requested
                prev_fails = self.load_previous()
                allow_list += prev_fails
                break

        self.root_zests, self.allow_to_run, find_errors = zest_finder.find_zests(
            root,
            include_dirs,
            allow_list,
            allow_files.split(":") if allow_files is not None else None,
            match_string,
            exclude_string,
            bypass_skip,
            groups.split(":") if groups is not None else None,
            exclude_groups.split(":") if exclude_groups is not None else None,
        )

        if preview:
            for i in sorted(self.allow_to_run):
                print(i)
            print(f"\nWould have run {len(self.allow_to_run)} tests")
            self.retcode = 1
            return

        self.handle_find_errors(find_errors)

    def handle_find_errors(self, find_errors):
        if len(find_errors) > 0:
            zest_display.display_find_errors(find_errors)
            self.retcode = 1

    def is_unlimited_run(self):
        """
        An unlimited run is one that has no constraints -- ir run everything.
        Int that case subclass code may choose the clear all caches.
        """
        return (
            self.allow_to_run == "__all__"
            and self.allow_files is None
            and self.match_string is None
            and self.groups is None
        )

    def load_previous(self):
        fails = {}
        for file in glob.glob(str(self.output_folder / "*")):
            with open(file) as f:
                for line in f:
                    res = json.loads(line)
                    # There can be multiple records from previous runs,
                    # accept the LASt state of the error run
                    fails[res.get("full_name")] = True if res.get("error") is not None else False
        return list(set([key for key, val in fails.items() if val]))

