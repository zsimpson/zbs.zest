"""
Console UI.
"""

import itertools
import copy
import time
import sys
import os
import re
import curses
from pathlib import Path
from collections import defaultdict
from zest.zest import log
from zest.zest_runner_multi_thread import ZestRunnerMultiThread, read_lines
from zest.zest_runner_single_thread import traceback_match_filename
from . import __version__

if os.name == "nt":
    import msvcrt
else:
    import select


scr = None
ansi_escape = re.compile(r"(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]")


def _kbhit():
    """
    Returns True if a keypress is waiting to be read in stdin, False otherwise.
    Base on: https://stackoverflow.com/a/55692274
    """
    if os.name == "nt":
        return msvcrt._kbhit()
    else:
        dr, dw, de = select.select([sys.stdin], [], [], 0)
        return dr != []


def _num_key_to_int(key):
    return ord(key) - ord("0")


# States
# ----------------------------------------------------------------------------

STOPPED = 0
RUNNING = 1
STOPPING = 2
WATCHING = 3
run_state_strs = [
    "Stopped",
    "Running",
    "Stopping (^C to force)",
    "Watching",
]


# Draw
# ----------------------------------------------------------------------------

PAL_NONE = 0
PAL_MENU = 1
PAL_MENU_KEY = 2
PAL_MENU_TITLE = 3
PAL_MENU_RUN_STATUS = 4
PAL_NAME = 5
PAL_NAME_SELECTED = 6
PAL_STATUS = 7
PAL_SUCCESS = 8
PAL_FAIL = 9
PAL_SKIPPED = 10
PAL_FAIL_KEY = 11

PAL_ERROR_LIB = 12
PAL_ERROR_PATHNAME = 13
PAL_ERROR_FILENAME = 14
PAL_ERROR_CONTEXT = 15
PAL_ERROR_MESSAGE = 16
PAL_ERROR_BASE = 17
PAL_ERROR_LINENO = 18
PAL_LINE = 19
PAL_STDOUT = 20
PAL_STDERR = 21
PAL_STATUS_KEY = 22

pal = [
    # PAL_NONE
    (-1, -1, 0),
    # PAL_MENU
    (curses.COLOR_BLACK, curses.COLOR_WHITE, 0),
    # PAL_MENU_KEY
    (curses.COLOR_RED, curses.COLOR_WHITE, curses.A_BOLD),
    # PAL_MENU_TITLE
    (curses.COLOR_BLUE, curses.COLOR_WHITE, 0),
    # PAL_MENU_AUTORUN_STATUS
    (curses.COLOR_CYAN, curses.COLOR_WHITE, 0),
    # PAL_NAME
    (curses.COLOR_CYAN, -1, 0),
    # PAL_NAME_SELECTED
    (curses.COLOR_CYAN, -1, curses.A_BOLD),
    # PAL_STATUS
    (curses.COLOR_CYAN, -1, 0),
    # PAL_SUCCESS
    (curses.COLOR_GREEN, -1, 0),
    # PAL_FAIL
    (curses.COLOR_RED, -1, curses.A_BOLD),
    # PAL_SKIPPED
    (curses.COLOR_YELLOW, -1, 0),
    # PAL_FAIL_KEY
    (curses.COLOR_RED, -1, curses.A_BOLD),
    # PAL_ERROR_LIB
    (curses.COLOR_BLACK, -1, curses.A_BOLD),
    # PAL_ERROR_PATHNAME
    (curses.COLOR_YELLOW, -1, 0),
    # PAL_ERROR_FILENAME
    (curses.COLOR_YELLOW, -1, curses.A_BOLD),
    # PAL_ERROR_CONTEXT
    (curses.COLOR_MAGENTA, -1, curses.A_BOLD),
    # PAL_ERROR_MESSAGE
    (curses.COLOR_RED, -1, curses.A_BOLD),
    # PAL_ERROR_BASE
    (curses.COLOR_WHITE, -1, 0),
    # PAL_ERROR_LINENO
    (curses.COLOR_YELLOW, -1, curses.A_BOLD),
    # PAL_LINE
    (curses.COLOR_RED, -1, curses.A_BOLD),
    # PAL_STDOUT
    (curses.COLOR_YELLOW, -1, 0),
    # PAL_STDERR
    (curses.COLOR_YELLOW, -1, curses.A_BOLD),
    # PAL_STATUS_KEY
    (curses.COLOR_RED, -1, curses.A_BOLD),
]


def _print(y, x, *args):
    def words_and_spaces(s):
        # Inspired by http://stackoverflow.com/a/8769863/262271
        return list(
            itertools.chain.from_iterable(zip(s.split(), itertools.repeat(" ")))
        )[:-1]

    height = curses.LINES
    width = curses.COLS
    _y = y
    _x = x
    mode = pal[PAL_MENU][2] | curses.color_pair(PAL_MENU)
    for arg in args:
        if isinstance(arg, int):
            mode = pal[arg][2] | curses.color_pair(arg)
        else:
            arg = str(arg)
            lines = arg.split("\n")
            for line_i, line in enumerate(lines):
                line = ansi_escape.sub("", line)
                if _y >= height:
                    break
                len_line = len(line)
                if _x + len_line <= width:
                    scr.addstr(_y, _x, line, mode)
                    _x += len_line
                else:
                    # Word-wrap
                    for word in words_and_spaces(line):
                        if len(word) + _x <= width:
                            scr.addstr(_y, _x, word, mode)
                        else:
                            _y += 1
                            _x = x
                            if y >= height - 1:
                                # Can't go down another line
                                break
                            scr.addstr(_y, _x, word, mode)
                        _x += len(word)
                if line_i > 0:
                    _x = x
                    _y += 1
    _y += 1
    return _y, _x


def draw_menu_fill_to_end_of_line(y, length):
    rows, cols = scr.getmaxyx()
    if cols - length > 0:
        scr.addstr(y, length, f"{' ':<{cols - length}}", curses.color_pair(PAL_MENU))


def draw_title_bar():
    y = 0
    _, length = _print(
        0,
        0,
        PAL_MENU_TITLE,
        f"Zest-Runner v{__version__}  ",
        PAL_MENU_KEY,
        "q",
        PAL_MENU,
        "uit   ",
        PAL_MENU,
        "run ",
        PAL_MENU_KEY,
        "a",
        PAL_MENU,
        "ll   ",
        PAL_MENU,
        "run ",
        PAL_MENU_KEY,
        "f",
        PAL_MENU,
        "ails   ",
    )
    draw_menu_fill_to_end_of_line(0, length)
    y += 1
    return y


def draw_status(y, run_state, match_string, current_running_tests_by_proc_i):
    _print(
        y,
        0,
        PAL_STATUS_KEY,
        "M",
        PAL_NONE,
        "atch   : ",
        PAL_STATUS,
        match_string or "",
        PAL_NONE,
        "",
    )
    y += 1

    # _print(
    #     y, 0, PAL_STATUS_KEY, "C", PAL_NONE, "apture : ", PAL_STATUS, str(capture),
    # )
    # y += 1

    proc_iz = sorted(current_running_tests_by_proc_i.keys())
    _print(
        y, 0, PAL_NONE, "Status  : ", PAL_STATUS, run_state_strs[run_state] + " ",
    )
    y += 1

    if len(proc_iz) > 0:
        # _print(
        #     y, 0, PAL_NONE, "Proc_i  : ",
        # )
        # y += 1
        for proc_i in proc_iz:
            name_stack = (current_running_tests_by_proc_i[proc_i] or "").split(".")
            _print(
                y,
                0,
                PAL_ERROR_LIB,
                f"{proc_i:>2}) ",
                PAL_NAME_SELECTED,
                name_stack[0],
                PAL_NAME,
                "" if len(name_stack[1:]) == 0 else ("." + ".".join(name_stack[1:])),
            )
            y += 1
    return y


def draw_summary(y, n_success, n_errors, n_skips):
    _print(
        y,
        0,
        PAL_NONE,
        "Last run: ",
        PAL_SUCCESS,
        "Success",
        PAL_NONE,
        " + ",
        PAL_FAIL,
        "Fails",
        PAL_NONE,
        " + ",
        PAL_SKIPPED,
        "Skipped",
        PAL_NONE,
        " = ",
        PAL_SUCCESS,
        str(n_success),
        PAL_NONE,
        " + ",
        PAL_FAIL,
        str(n_errors),
        PAL_NONE,
        " + ",
        PAL_SKIPPED,
        str(n_skips),
        PAL_NONE,
        " = ",
        PAL_NONE,
        str(n_success + n_errors + n_skips),
    )
    y += 1
    return y


def draw_fail_lines(y, errors, root, show_result_full_name):
    n_errors = len(errors)
    if n_errors > 0:
        _print(y, 0, PAL_NONE, f"Failed tests:")
        y += 1

        for i, error in enumerate(errors):
            if i >= 10:
                break

            name = error["full_name"]
            formatted = error["error_formatted"]

            lines = []
            for line in formatted:
                lines += [sub_line for sub_line in line.strip().split("\n")]
            last_filename_line = ""
            if len(lines) >= 3:
                last_filename_line = lines[-3]
            split_line = traceback_match_filename(root, last_filename_line)
            if split_line:
                leading, basename, lineno, context, is_libs = split_line

                selected = (
                    show_result_full_name is not None and show_result_full_name == name
                )
                _print(
                    y,
                    0,
                    PAL_FAIL_KEY,
                    str(i + 1),
                    PAL_NONE,
                    " ",
                    PAL_NAME_SELECTED if selected else PAL_NAME,
                    name,
                    PAL_ERROR_BASE,
                    " raised: ",
                    PAL_ERROR_MESSAGE,
                    error["error"],
                    PAL_ERROR_BASE,
                    " ",
                    PAL_ERROR_PATHNAME,
                    basename,
                    PAL_ERROR_BASE,
                    ":",
                    PAL_ERROR_PATHNAME,
                    str(lineno),
                )
                y += 1

        if n_errors > 9:
            _print(y, 0, PAL_ERROR_BASE, f"+ {n_errors - 9} more")
            y += 1
    return y


def draw_warnings(y, warnings):
    for i, warn in enumerate(warnings):
        _print(
            y, 0, PAL_ERROR_BASE, f"WARNING {i}: {warn}",
        )
        y += 1
    return y


def draw_result_details(y, root, result, stdout, stderr):
    if result is None:
        return y

    # if run_state == WATCHING:
    #     _, length = _print(
    #         y, 0, PAL_MENU, "Watching: ", PAL_MENU_RUN_STATUS, watch_file,
    #     )
    #     draw_menu_fill_to_end_of_line(y, length)
    #     y += 1

    _, length = _print(
        y, 0, PAL_MENU, "Test result: ", PAL_MENU_RUN_STATUS, result["full_name"],
    )
    draw_menu_fill_to_end_of_line(y, length)
    y += 1

    _, length = _print(
        y,
        0,
        PAL_MENU_KEY,
        "r",
        PAL_MENU,
        "e-run this test   ",
        PAL_MENU_KEY,
        "w",
        PAL_MENU,
        "atch test file (auto-re-run)   ",
    )
    draw_menu_fill_to_end_of_line(y, length)
    y += 1

    if result["is_running"] is True:
        _print(y, 0, PAL_NONE, "Runnning...")
        y += 1
    elif result["error"] is None:
        _print(y, 0, PAL_SUCCESS, "Passed!")
        y += 1
    else:
        formatted = result["error_formatted"]
        lines = []
        for line in formatted:
            lines += [sub_line for sub_line in line.strip().split("\n")]

        is_libs = False
        for line in lines[1:-1]:
            s = []
            split_line = traceback_match_filename(root, line)
            if split_line is None:
                s += [PAL_ERROR_LIB if is_libs else PAL_ERROR_BASE, line]
            else:
                leading, basename, lineno, context, is_libs = split_line
                if is_libs:
                    s += [PAL_ERROR_LIB, "File ", leading, "/", basename]
                    s += [PAL_ERROR_LIB, ":", str(lineno)]
                    s += [PAL_ERROR_LIB, " in function "]
                    s += [PAL_ERROR_LIB, context]
                else:
                    s += [
                        PAL_ERROR_BASE,
                        "File ",
                        PAL_ERROR_PATHNAME,
                        leading,
                        PAL_ERROR_BASE,
                        "/ ",
                        PAL_ERROR_FILENAME,
                        basename,
                        PAL_ERROR_BASE,
                        ":",
                        PAL_ERROR_LINENO,
                        str(lineno),
                        PAL_ERROR_BASE,
                        " in function ",
                    ]
                    s += [PAL_ERROR_MESSAGE, context]
            _print(y, 0, *s)
            y += 1

        s = [
            PAL_ERROR_BASE,
            "raised: ",
            PAL_ERROR_MESSAGE,
            result["error"],
        ]
        _print(y, 0, *s)
        y += 1

        error_message = str(result["error"]).strip()
        if error_message != "":
            _print(y, 4, PAL_ERROR_MESSAGE, error_message)

        if stdout is not None and stdout != "":
            y += 1
            y, _ = _print(y, 0, PAL_NONE, "Stdout:")
            y, _ = _print(y, 0, PAL_STDOUT, "".join(stdout))
            y += 1

        if stderr is not None and stderr != "":
            y += 1
            y, _ = _print(y, 0, PAL_NONE, "Stderr:")
            y, _ = _print(y, 0, PAL_STDOUT, "".join(stderr))
            y += 1

    return y


def load_results(zest_results_path):
    results = {}
    errors = []
    stdouts = defaultdict(list)
    stderrs = defaultdict(list)
    for res_path in os.listdir(zest_results_path):
        res_path = zest_results_path / res_path
        is_out = Path(res_path).suffix == ".out"
        with open(res_path) as fd:
            full_name = None
            for payload in read_lines(fd, include_stdio=True):
                if isinstance(payload, dict):
                    is_running = payload["is_running"]
                    if is_running is True:
                        full_name = payload["full_name"]
                    elif is_running is False and is_out:
                        # checking is_out to prevent storing errors twice (stdout and stderr)
                        results[payload["full_name"]] = payload
                        if payload["error"] is not None:
                            errors += [payload]
                elif isinstance(payload, str):
                    if is_out:
                        stdouts[full_name] += [payload]
                    else:
                        stderrs[full_name] += [payload]

    return results, errors, stdouts, stderrs


def _run(
    _scr,
    root=".",
    include_dirs=None,
    allow_to_run=None,
    match_string=None,
    exclude_string=None,
    n_workers=1,
    **kwargs,
):
    global scr
    scr = _scr
    num_keys = [str(i) for i in range(1, 10)]
    run_state = None
    dirty = True
    current_running_tests_by_proc_i = {}
    n_success = 0
    n_errors = 0
    n_skips = 0
    show_result_full_name = None
    run_state = STOPPED
    warnings = []
    runner = None
    request_run = None
    request_stop = False
    request_end = False
    zest_results_path = Path(".zest_results")
    results, errors, stdouts, stderrs = None, None, None, None
    allow_to_run_list = [] if allow_to_run is None else allow_to_run.split(":")

    log(f"CWD={os.getcwd()}")

    def render():
        nonlocal dirty
        if not dirty:
            return
        dirty = False
        scr.clear()
        y = draw_title_bar()
        y = draw_status(y, run_state, match_string, current_running_tests_by_proc_i)
        y = draw_summary(y, n_success, n_errors, n_skips)
        y = draw_warnings(y, warnings)
        draw_fail_lines(y + 1, errors, root, show_result_full_name)
        y = draw_result_details(
            y + 13,
            root,
            results.get(show_result_full_name),
            stdouts.get(show_result_full_name),
            stderrs.get(show_result_full_name),
        )
        scr.refresh()

    def callback(payload):
        nonlocal dirty, current_running_tests_by_proc_i, n_errors, n_success
        dirty = True
        log("callback", payload.get("state"), payload.get("full_name"),)
        state = payload.get("state")
        full_name = payload.get("full_name")
        proc_i = payload.get("proc_i", 0)
        current_running_tests_by_proc_i[proc_i] = f"{state}: {full_name}"
        if state == "stopped":
            if payload.get("error") is not None:
                n_errors += 1
            else:
                n_success += 1

    def update_run_state():
        """
        This is the state machine that is called by the main ui thread "zest_ui_thread".
        All transitions of state are made here. Other code can set the "request_*" properties
        but the state only changes here.
        """

        def new_state(state):
            nonlocal run_state, dirty
            run_state = state
            dirty = True

        def start_run(allow_to_run):
            nonlocal runner, n_errors, n_success, n_skips
            assert runner is None
            n_errors, n_success, n_skips = 0, 0, 0

            if allow_to_run in allow_to_run_list or len(allow_to_run_list) == 0:
                log("start multi from ui")
                runner = ZestRunnerMultiThread(
                    zest_results_path,
                    callback=callback,
                    root=root,
                    include_dirs=include_dirs,
                    allow_to_run=allow_to_run,
                    match_string=match_string,
                    exclude_string=exclude_string,
                    n_workers=n_workers,
                )

        nonlocal request_run, request_stop, runner
        nonlocal results, errors, stdouts, stderrs
        if run_state == STOPPED:
            # Tests are done. The runner_thread should be stopped
            # Ways out:
            #    * request_end can terminate
            #    * request_run can start a new run
            if request_end:
                return False

            if request_run is not None:
                start_run(request_run)
                request_run = None
                new_state(RUNNING)

        elif run_state == RUNNING:
            # Tests are running.
            # Ways out:
            #    * request_end: Goto STOPPING
            #    * the "runner_thread" has terminated. Goto STOPPED
            #    * a new run is requested before the current run has terminated. Goto STOPPING

            running = runner.poll(request_stop)
            if not running or request_end or request_run is not None:
                new_state(STOPPING)

        elif run_state == STOPPING:
            # Trying to stop.
            # Ways out:
            #   * The runner has terminated. Goto STOPPED
            running = runner.poll(True)
            if not running:
                runner = None
                new_state(STOPPED)
                results, errors, stdouts, stderrs = load_results(zest_results_path)

        # elif run_state == WATCHING:
        #     if watch_timestamp != os.path.getmtime(watch_file):
        #         request_run = ".".join(request_watch[2])
        #     if request_run is not None:
        #         run_state = STOPPED
        #         watch_timestamp = None
        #         watch_file = None
        #         request_watch = None
        #         dirty = True

        return True  # True means keep running

    curses.use_default_colors()
    for i, p in enumerate(pal):
        if i > 0:
            curses.init_pair(i, pal[i][0], pal[i][1])

    zest_results_path.mkdir(parents=True, exist_ok=True)
    results, errors, stdouts, stderrs = load_results(zest_results_path)

    while True:
        try:
            if not update_run_state():
                break

            render()
            if _kbhit():
                key = scr.getkey()

                if key in num_keys:
                    error_i = (
                        _num_key_to_int(key) - 1
                    )  # Because they press '1' but mean index '0'
                    if 0 <= error_i < len(errors):
                        error = errors[error_i]
                        if show_result_full_name == error["full_name"]:
                            # Already showing, hide it
                            show_result_full_name = None
                        else:
                            show_result_full_name = error["full_name"]
                        dirty = True

                if key == "q":
                    request_end = True

                if key == "a":
                    request_run = "__all__"
                    dirty = True

                if key == "f":
                    request_run = "__failed__"
                    dirty = True

                if key == "r":
                    request_run = show_result_full_name
                    dirty = True

                if key == "w":
                    if show_result_full_name is not None:
                        request_watch = show_result_full_name
                        dirty = True

                if key == "m":
                    curses.echo()
                    scr.move(1, 10)
                    scr.clrtoeol()
                    s = scr.getstr(1, 10, 15).decode("ascii")
                    curses.noecho()
                    match_string = s
                    dirty = True

            time.sleep(0.01)

        except KeyboardInterrupt:
            if request_end:
                break

            request_end = True


def run(**kwargs):
    # HACK
    curses.wrapper(_run, **kwargs)
