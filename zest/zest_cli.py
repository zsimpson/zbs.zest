"""
CLI entrypoint for UI, single-threaded and multi-threaded runners.
"""

import time
import os
import sys
import argparse
import pathlib
from zest import zest_runner_single_thread
from zest.zest_runner_multi_thread import ZestRunnerMultiThread, ZestRunnerErrors
from zest.zest_display import display_errors, display_complete
from zest import zest_console_ui
from . import __version__


def main():
    parser = argparse.ArgumentParser()

    # fmt: off
    parser.add_argument("--version", action="store_true",
        help="Show version and exit",
    )

    parser.add_argument("--root", nargs="?", default=os.getcwd(),
        help="Optional root directory to search (default: cwd).",
    )

    parser.add_argument("--include_dirs", nargs="?", default=".",
        help="Optional colon-delimited list of directories to search.",
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

    parser.add_argument("exclude_string", type=str, nargs="?",
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

    parser.add_argument("--add_markers", action="store_true",
        help="For internal debugging."
    )

    parser.add_argument("--bypass_skip", nargs="?", default="",
        help="For internal debugging."
    )
    # fmt: on

    kwargs = vars(parser.parse_args())

    if kwargs.pop("version", None):
        print(__version__)
        sys.exit(0)

    if kwargs.pop("ui", False):
        retcode = zest_console_ui.run(**kwargs)
    else:
        if kwargs.get("n_workers") > 1:

            # TODO: Functionalize this over to multi_thrasd.py

            call_log = []
            call_errors = []

            def callback(payload):
                nonlocal call_log, call_errors
                state = payload["state"]
                if state == "stopped":
                    print(f".")
                    call_log += [payload["full_name"]]
                    call_errors += [
                        (
                            payload["error"],
                            payload["error_formatted"],
                            payload["full_name"].split("."),
                        )
                    ]

            zest_results_path = pathlib.Path(".zest_results")
            zest_results_path.mkdir(parents=True, exist_ok=True)
            try:
                runner = ZestRunnerMultiThread(zest_results_path, callback, **kwargs)
                request_stop = False
                retcode = 0
                while True:
                    try:
                        # if ...: request_stop = True
                        if not runner.poll(request_stop):
                            break
                        time.sleep(0.1)
                    except KeyboardInterrupt:
                        request_stop = True
                        retcode = 1

                display_complete("", call_log, call_errors)
            except ZestRunnerErrors as e:
                display_errors(e.errors)
                retcode = 1

        else:
            retcode = zest_runner_single_thread.run_zests(**kwargs)

    sys.exit(retcode)


if __name__ == "__main__":
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
