"""
This shim is called as a subprocesss from zest_runner_multi_thread
and its job is to load a single root zest and run it while
writing status messages into the stdio streams.
"""


import sys
import dataclasses
from importlib import util
import json
from zest import zest_finder
from zest import zest


def emit(dict_, full_name, stream):
    try:
        print("@@@" + json.dumps(dict_) + "@@@", file=stream)
    except TypeError:
        dict_ = dict(full_name=full_name, error="Serialization error")
        print("@@@" + json.dumps(dict_) + "@@@", file=stream)


def emit_both_streams(dict_, full_name):
    emit(dict_, full_name, sys.stdout)
    emit(dict_, full_name, sys.stderr)


def event_callback(zest_result):
    dict_ = dict(
        full_name=zest_result.full_name,
        error=repr(zest_result.error) if zest_result.error is not None else None,
        error_formatted=zest_result.error_formatted,
        is_running=zest_result.is_running,
    )
    emit_both_streams(dict_, zest_result.full_name)


if __name__ == "__main__":
    root_name, module_name, full_path = sys.argv[1:4]
    root_zest_func = zest_finder.load_module(root_name, module_name, full_path)

    zest.do(
        root_zest_func,
        test_start_callback=event_callback,
        test_stop_callback=event_callback,
        allow_to_run=None,
    )
