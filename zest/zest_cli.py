"""
CLI entrypoint for UI, single-threaded and multi-threaded runners.
"""

import time
import os
import sys
import argparse
import pathlib
import json
import logging
import logging.config
from pathlib import Path
from zest.zest_runner_single_thread import ZestRunnerSingleThread
from zest.zest_runner_multi_thread import ZestRunnerMultiThread
from zest.zest_display import display_find_errors, display_complete
from zest import zest_console_ui
from zest.zest import log
from . import __version__


def main():
    # log("CLI MAIN")
    parser = argparse.ArgumentParser()

    # fmt: off
    parser.add_argument("--version", action="store_true",
        help="Show version and exit",
    )

    parser.add_argument("--output_folder", nargs="?", default=".zest_results",
        help="Where to store results",
    )

    parser.add_argument("--root", nargs="?", default=os.getcwd(),
        help="Optional root directory to search (default: cwd).",
    )

    parser.add_argument("--include_dirs", nargs="?", default=".",
        help="Optional colon-delimited list of directories to search.",
    )

    parser.add_argument("--allow_files", nargs="?",
        help=(
            "Optional colon-delimited list of filenames "
            "that will be allowed to run. Special: '__all__'."
        )
    )

    parser.add_argument("--allow_to_run", nargs="?", default="__all__",
        help=(
            "Optional colon-delimited list of full test names (eg: 'zest_name.it_tests') "
            "that will be allowed to run. Specials: '__all__', '__failed__'."
        )
    )

    parser.add_argument("match_string", type=str, nargs="?",
        help="Optional substring that must be present in a test to run."
    )

    parser.add_argument("--exclude_string", type=str, nargs="?",
        help="Optional substring that must be absent in a test to run."
    )

    parser.add_argument("--verbose", default=1, type=int,
        help="0=silent, 1=dot-mode, 2=run-trace 3=full-trace",
    )

    parser.add_argument("--disable_shuffle", action="store_true",
        help="Disable the shuffling of test order.",
    )

    parser.add_argument("--n_workers", default=1, type=int,
        help="Number of parallel processes.",
    )

    parser.add_argument("--capture", action="store_true",
        help="Capture all stdio.",
    )

    parser.add_argument("--ui", action="store_true",
        help="Use console UI.",
    )

    parser.add_argument("--go", action="store_true",
        help="Use console UI and start the run upon entry.",
    )

    parser.add_argument("--debug_mode", action="store_true",
        help="Start console in debug_mode.",
    )

    parser.add_argument("--add_markers", action="store_true",
        help="For internal debugging."
    )

    parser.add_argument("--bypass_skip", nargs="?", default="",
        help="For internal debugging."
    )

    parser.add_argument("--groups", nargs="?",
        help="Optional colon-delimited list of groups to run.",
    )

    parser.add_argument("--exclude_groups", nargs="?",
        help="Optional colon-delimited list of groups to exclude.",
    )

    parser.add_argument("--common_tmp", nargs="?", type=str,
        help="If specified, use this folder as CWD for all. Default is a folder per-test",
    )

    parser.add_argument("--tmp_root", nargs="?", type=str, default="/tmp",
        help="If specified, use this folder as the root for all per-tests",
    )

    # parser.add_argument("--logger_config_json", nargs="?", type=str, default=None,
    #     help="If specified, load logger config from specified json file",
    # )

    # fmt: on

    kwargs = vars(parser.parse_args())

    if kwargs.pop("version", None):
        print(__version__)
        sys.exit(0)

    # I had the idea that you got to configure the loggers in zest
    # but as I dealt with all the problems of capturing
    # this became less feasible. For example, if a logger
    # is attached to stdout and I try to capture stdout
    # then the logger will spew out a message about logging
    # to a closed handle.
    # I think the correct solution is that the caller of
    # zest does not get to configure logging but rather
    # zest runner creates a single root logger that
    # traps everything and can be viewed like any other
    # asset of the testing runs.

    # log_json = kwargs.get("logger_config_json")
    # if log_json is not None:
    #     with open(log_json) as f:
    #         contents = json.loads(f.read())
    #         logging.config.dictConfig(contents)
    #         # log("LOGGING", json.dumps(contents, indent=4))

    if kwargs.pop("ui", False) or kwargs.get("go", False):
        retcode = zest_console_ui.run(**kwargs)
    else:
        if kwargs.get("n_workers") > 1:
            runner = ZestRunnerMultiThread(**kwargs)
            from zest.zest import zest

            runner.message_pump()
        else:
            runner = ZestRunnerSingleThread(**kwargs)
        retcode = runner.retcode

    sys.exit(retcode)


if __name__ == "__main__":
    # log("START")
    allow_reentrancy = True
    if allow_reentrancy:
        main()
    else:
        pidfile = f"{Path.home()}/zest_runner.pid"
        pid = str(os.getpid())
        if os.path.isfile(pidfile):
            print(f"{pidfile} already exists {sys.argv}", file=sys.stderr)
            sys.exit(1)

        with open(pidfile, "w") as f:
            f.write(pid)

        try:
            main()
        finally:
            found_pid = 0
            with open(pidfile) as f:
                try:
                    found_pid = f.read()
                except Exception as e:
                    pass
            if str(found_pid) == str(pid):
                os.unlink(pidfile)
