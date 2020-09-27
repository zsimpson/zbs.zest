"""
Controls console based UI.
"""


import itertools
import copy
import time
import sys
import os
import curses
import threading
from zest.zest_runner import log, ZestRunner, run_lock
from . import __version__

if os.name == "nt":
    import msvcrt
else:
    import select


def kbhit():
    """
    Returns True if a keypress is waiting to be read in stdin, False otherwise.
    Base on: https://stackoverflow.com/a/55692274
    """
    if os.name == "nt":
        return msvcrt.kbhit()
    else:
        dr, dw, de = select.select([sys.stdin], [], [], 0)
        return dr != []

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

def print(y, x, *args):
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
            len_arg = len(arg)
            if _x + len_arg <= width:
                scr.addstr(_y, _x, arg, mode)
                _x += len_arg
            else:
                # Word-wrap
                for word in words_and_spaces(arg):
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
    _y += 1
    return _y, _x

def draw_menu_fill_to_end_of_line(y, length):
    rows, cols = scr.getmaxyx()
    if cols - length > 0:
        scr.addstr(
            y, length, f"{' ':<{cols - length}}", curses.color_pair(PAL_MENU)
        )

def draw_title_bar():
    y = 0
    _, length = print(
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

def draw_status(y):
    print(
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

    print(
        y,
        0,
        PAL_STATUS_KEY,
        "C",
        PAL_NONE,
        "apture : ",
        PAL_STATUS,
        str(capture),
    )
    y += 1

    pids = sorted(current_running_tests_by_pid.keys())
    print(
        y,
        0,
        PAL_NONE,
        "Status  : ",
        PAL_STATUS,
        run_state_strs[run_state] + " ",
    )
    y += 1

    if len(pids) > 0:
        print(
            y, 0, PAL_NONE, "Pids    : ",
        )
        y += 1
        for pid in pids:
            name_stack = (current_running_tests_by_pid[pid] or "").split(".")
            print(
                y,
                0,
                PAL_ERROR_LIB,
                f"{pid:>5}) ",
                PAL_NAME_SELECTED,
                name_stack[0],
                PAL_NAME,
                ".".join(name_stack[1:]),
            )
            y += 1
    return y

def draw_summary(y):
    print(
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

def draw_fail_lines(y):
    result_by_shortcut_number = {}
    if n_errors > 0:
        print(y, 0, PAL_NONE, f"Failed tests:")
        y += 1

        error_i = 0
        with run_lock:
            results = copy.copy(results)

        for i, (name, result) in enumerate(results.items()):
            if result is not None and result.error is not None:
                error_i += 1
                if error_i < 10:
                    result_by_shortcut_number[error_i] = result
                    formatted = result.error_formatted
                    lines = []
                    for line in formatted:
                        lines += [sub_line for sub_line in line.strip().split("\n")]
                    last_filename_line = ""
                    if len(lines) >= 3:
                        last_filename_line = lines[-3]
                    split_line = _traceback_match_filename(last_filename_line)
                    if split_line:
                        leading, basename, lineno, context, is_libs = split_line

                        selected = (
                            show_result_full_name is not None
                            and show_result_full_name == name
                        )
                        print(
                            y,
                            0,
                            PAL_FAIL_KEY,
                            str(error_i),
                            PAL_NONE,
                            " ",
                            PAL_NAME_SELECTED if selected else PAL_NAME,
                            name,
                            PAL_ERROR_BASE,
                            " raised: ",
                            PAL_ERROR_MESSAGE,
                            result.error.__class__.__name__,
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
            print(y, 0, PAL_ERROR_BASE, f"+ {n_errors - 9} more")
            y += 1
    return y

def draw_warnings(y):
    for i, warn in enumerate(warnings):
        print(
            y, 0, PAL_ERROR_BASE, f"WARNING {i}: {warn}",
        )
        time.sleep(1)  # HACK
        y += 1
    return y

def draw_result_details(y):
    with run_lock:
        result = results.get(show_result_full_name)

    if result is None:
        return y

    if run_state == WATCHING:
        _, length = print(
            y,
            0,
            PAL_MENU,
            "Watching: ",
            PAL_MENU_RUN_STATUS,
            watch_file,
        )
        draw_menu_fill_to_end_of_line(y, length)
        y += 1
    elif show_result_full_name is not None:
        _, length = print(
            y,
            0,
            PAL_MENU,
            "Test result: ",
            PAL_MENU_RUN_STATUS,
            show_result_full_name,
        )
        draw_menu_fill_to_end_of_line(y, length)
        y += 1

    _, length = print(
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

    if result.is_running is True:
        print(y, 0, PAL_NONE, "Runnning...")
        y += 1
    elif result.error is None:
        print(y, 0, PAL_SUCCESS, "Passed!")
        y += 1
    else:
        formatted = result.error_formatted
        lines = []
        for line in formatted:
            lines += [sub_line for sub_line in line.strip().split("\n")]

        is_libs = False
        for line in lines[1:-1]:
            s = []
            split_line = _traceback_match_filename(line)
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
            print(y, 0, *s)
            y += 1

        s = [
            PAL_ERROR_BASE,
            "raised: ",
            PAL_ERROR_MESSAGE,
            result.error.__class__.__name__,
        ]
        print(y, 0, *s)
        y += 1

        error_message = str(result.error).strip()
        if error_message != "":
            print(y, 4, PAL_ERROR_MESSAGE, error_message)

        if result.stdout is not None and result.stdout != "":
            y += 1
            y, _ = print(y, 0, PAL_NONE, "Stdout:")
            y, _ = print(y, 0, PAL_STDOUT, result.stdout)
            y += 1

        if result.stderr is not None and result.stderr != "":
            y += 1
            y, _ = print(y, 0, PAL_NONE, "Stderr:")
            y, _ = print(y, 0, PAL_STDOUT, result.stderr)
            y += 1

    return y

'''
def event_complete(self):
    self.complete = True
    self.dirty = True

def event_request_stop(self):
    return self.request_stop

def event_test_start(self, zest_result):
    super().event_test_start(zest_result)
    self.dirty = True
    self.current_running_tests_by_pid[zest_result.pid] = " . ".join(
        zest_result.call_stack
    )

def event_test_stop(self, zest_result):
    super().event_test_stop(zest_result)
    self.dirty = True
    self.current_running_tests_by_pid[zest_result.pid] = None
    if zest_result.error is not None:
        self.n_errors += 1
    else:
        self.n_success += 1
'''


def runner_thread_is_running(self):
    return self.runner_thread is not None and self.runner_thread.is_alive()

def num_key_to_int(self, key):
    return ord(key) - ord("0")

def s(self, *strs):
    self.warnings += ["".join([str(s) for s in strs if not s.startswith("\u001b")])]


def _run(scr, **kwargs):
    show_result_full_name = None
    request_run = None
    request_stop = False
    scr = None
    runner_thread = None
    current_running_tests_by_pid = None
    n_success = None
    n_errors = None
    n_skips = None
    complete = None
    key = None
    num_keys = [str(i) for i in range(1, 10)]
    run_state = None
    watch_file = None
    watch_timestamp = None
    result_by_shortcut_number = None
    verbose = 0
    capture = False
    warnings = []
    scr = scr
    runner_thread = None
    dirty = True
    current_running_tests_by_pid = {}
    n_success = 0
    n_errors = 0
    n_skips = 0
    complete = False
    key = None
    show_result_full_name = None
    run_state = STOPPED
    watch_file = None
    watch_timestamp = None

    request_run = None
    request_watch = None
    request_stop = False
    request_end = False

    def render():
        nonlocal dirty
        if not dirty:
            return
        dirty = False
        scr.clear()
        y = draw_title_bar()
        y = draw_status(y)
        y = draw_summary(y)
        y = draw_warnings(y)
        draw_fail_lines(y + 1)
        y = draw_result_details(y + 13)
        scr.refresh()

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

        def join_runner_thread():
            nonlocal runner_thread
            runner_thread.join()
            runner_thread = None

        if run_state == STOPPED:
            # Tests are done. The runner_thread should be stopped
            # Ways out:
            #    * request_end can terminate
            #    * request_run can start a new run
            assert runner_thread is None
            assert pool is None

            if request_end:
                return False

            if request_run is not None:
                with run_lock:
                    results = {}
                allow_to_run = request_run
                runner_thread_start()
                request_run = None
                new_state(RUNNING)

        elif run_state == RUNNING:
            # Tests are running.
            # Ways out:
            #    * request_end: Goto STOPPING
            #    * the "runner_thread" has terminated. Goto STOPPED
            #    * a new run is requested before the current run has terminated. Goto STOPPING
            if not runner_thread_is_running():
                join_runner_thread()
                new_state(STOPPED)

            elif request_end:
                request_stop = True
                new_state(STOPPING)

            elif request_run is not None:
                # Request of a new run, need to stop the previous one first
                request_stop = True
                new_state(STOPPING)

        elif run_state == STOPPING:
            # Trying to stop.
            # Ways out:
            #   * The "runner_thread" has terminated. Goto STOPPED
            request_stop = True
            if not runner_thread_is_running():
                join_runner_thread()
                new_state(STOPPED)

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

    while True:
        try:
            if not update_run_state():
                break

            render()
            if kbhit():
                key = scr.getkey()

                if key in num_keys:
                    show_details_i = num_key_to_int(key)
                    if 1 <= show_details_i < n_errors + 1:
                        with run_lock:
                            if result_by_shortcut_number is not None:
                                result = result_by_shortcut_number.get(
                                    show_details_i
                                )
                                if result is not None:
                                    if (
                                        show_result_full_name
                                        == result.full_name
                                    ):
                                        # Already showing, hide it
                                        show_result_full_name = None
                                    else:
                                        show_result_full_name = (
                                            result.full_name
                                        )
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

            time.sleep(0.05)

        except KeyboardInterrupt:
            if request_end:
                break

            request_end = True

def run(**kwargs):
    threading.current_thread().name = "zest_ui_thread"
    curses.wrapper(_run, **kwargs)
