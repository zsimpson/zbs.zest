"""
This shim is called as a subprocesss from zest_runner
and its job is to load a single root zest and run it while
writing status in to the same log file.
"""


import sys
import dataclasses
from importlib import util
import json
from zest import zest


def _load_module(root_name, module_name, full_path):
    # TODO: Add cache here?
    spec = util.spec_from_file_location(module_name, full_path)
    mod = util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return getattr(mod, root_name)


def event_callback(zest_result):
    print("@@@" + json.dumps(dataclasses.asdict(zest_result)) + "@@@")




if __name__ == "__main__":
    root_name, module_name, full_path = sys.argv[0:3]
    root_zest_func = _load_module(root_name, module_name, full_path)

    zest.do(
        root_zest_func,
        test_start_callback=event_callback,
        test_stop_callback=event_callback,
        allow_to_run=None,
    )




