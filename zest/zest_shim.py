"""
This shim is called as a subprocesss from zest_runner
and its job is to load a single root zest and run it while
writing status in to the same log file.
"""


import sys
import dataclasses
from importlib import util
import json
import zest_finder
from zest import zest


def event_callback(zest_result):
    print("@@@" + json.dumps(dataclasses.asdict(zest_result)) + "@@@")


if __name__ == "__main__":
    root_name, module_name, full_path = sys.argv[1:4]
    root_zest_func = zest_finder.load_module(root_name, module_name, full_path)

    zest.do(
        root_zest_func,
        test_start_callback=event_callback,
        test_stop_callback=event_callback,
        allow_to_run=None,
    )
