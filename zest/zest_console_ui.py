import itertools
import copy
import time
import sys
import os
import curses
import threading
from zest.zest_runner import log, ZestRunner
from . import __version__
if os.name == 'nt':
    import msvcrt
else:
    import select


def kbhit():
    '''
    Returns True if a keypress is waiting to be read in stdin, False otherwise.
    Base on: https://stackoverflow.com/a/55692274
    '''
    if os.name == 'nt':
        return msvcrt.kbhit()
    else:
        dr, dw, de = select.select([sys.stdin], [], [], 0)
        return dr != []


run_lock = threading.Lock()


class ZestConsoleUI(ZestRunner):
    """
    Controls console based UI.
    States:
        * main_menu
        * focus: "running/re-running" a test until pass
            - on fail, waits for a file change or 'enter' to re-run
            - on success or "n", moves to next
        * inspection: look at and re-run failed tests
            - 1-9 keys move into focus mode
        * running:
            Shows a status of number of tests run and cumuative errors

    A state implies a set of keyboard commands and a layout
    """

    STOPPED = 0
    RUNNING = 1
    STOPPING = 2
    WATCHING = 3
    run_state_strs = [
        "Stopped",
        "Running",
        "Stopping",
        "Watching",
    ]

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

    # Event overloads from super class
    def event_considering(self, root_name, module_name, package):
        pass

    def event_skip(self, root_name):
        pass

    def event_running(self, root_name):
        pass

    def event_not_running(self, root_name):
        pass

    def event_complete(self):
        self.complete = True
        self.dirty = True

    def event_request_stop(self):
        return self.request_stop

    def event_test_start(self, zest_result):
        super().event_test_start(zest_result)
        self.dirty = True
        self.current_running_tests_by_pid[zest_result.pid] = " . ".join(zest_result.call_stack)

    def event_test_stop(self, zest_result):
        super().event_test_stop(zest_result)
        self.dirty = True
        self.current_running_tests_by_pid[zest_result.pid] = None
        if zest_result.error is not None:
            self.n_errors += 1
        else:
            self.n_success += 1

    def print(self, y, x, *args):
        def words_and_spaces(s):
            # Inspired by http://stackoverflow.com/a/8769863/262271
            return list(itertools.chain.from_iterable(zip(s.split(), itertools.repeat(" "))))[:-1]

        height = curses.LINES
        width = curses.COLS
        _y = y
        _x = x
        mode = self.pal[self.PAL_MENU][2] | curses.color_pair(self.PAL_MENU)
        for arg in args:
            if isinstance(arg, int):
                mode = self.pal[arg][2] | curses.color_pair(arg)
            else:
                arg = str(arg)
                len_arg = len(arg)
                if _x + len_arg <= width:
                    self.scr.addstr(_y, _x, arg, mode)
                    _x += len_arg
                else:
                    # Word-wrap
                    for word in words_and_spaces(arg):
                        if len(word) + _x <= width:
                            self.scr.addstr(_y, _x, word, mode)
                        else:
                            _y += 1
                            _x = x
                            if y >= height - 1:
                                # Can't go down another line
                                break
                            self.scr.addstr(_y, _x, word, mode)
                        _x += len(word)
        _y += 1
        return _y, _x

    def draw_menu_fill_to_end_of_line(self, y, length):
        rows, cols = self.scr.getmaxyx()
        if cols - length > 0:
            self.scr.addstr(y, length, f"{' ':<{cols - length}}", curses.color_pair(self.PAL_MENU))

    def draw_title_bar(self):
        y = 0
        _, length = self.print(
            0, 0,
            self.PAL_MENU_TITLE, f"Zest-Runner v{__version__}  ",
            self.PAL_MENU_KEY, "q",
            self.PAL_MENU, "uit   ",
            self.PAL_MENU, "run ",
            self.PAL_MENU_KEY, "a",
            self.PAL_MENU, "ll   ",
            self.PAL_MENU, "run ",
            self.PAL_MENU_KEY, "f",
            self.PAL_MENU, "ails   ",
        )
        self.draw_menu_fill_to_end_of_line(0, length)
        y += 1
        return y

    def draw_status(self, y):
        self.print(
            y, 0,
            self.PAL_STATUS_KEY, "M",
            self.PAL_NONE, "atch   : \"",
            self.PAL_STATUS, self.match_string,
            self.PAL_NONE, "\"",
        )
        y += 1

        pids = sorted(self.current_running_tests_by_pid.keys())
        self.print(
            y, 0,
            self.PAL_NONE, "Status  : ",
            self.PAL_STATUS, self.run_state_strs[self.run_state] + " ",
        )
        y += 1

        if len(pids) > 0:
            self.print(
                y, 0,
                self.PAL_NONE, "Pids    : ",
            )
            y += 1
            for pid in pids:
                name_stack = (self.current_running_tests_by_pid[pid] or "").split(".")
                self.print(
                    y, 0,
                    self.PAL_ERROR_LIB, f"{pid:>5}) ",
                    self.PAL_NAME_SELECTED, name_stack[0],
                    self.PAL_NAME, ".".join(name_stack[1:]),
                )
                y += 1
        return y

    def draw_summary(self, y):
        self.print(
            y, 0,
            self.PAL_NONE, "Last run: ",
            self.PAL_SUCCESS, "Success",
            self.PAL_NONE, " + ",
            self.PAL_FAIL, "Fails",
            self.PAL_NONE, " + ",
            self.PAL_SKIPPED, "Skipped",
            self.PAL_NONE, " = ",
            self.PAL_SUCCESS, str(self.n_success),
            self.PAL_NONE, " + ",
            self.PAL_FAIL, str(self.n_errors),
            self.PAL_NONE, " + ",
            self.PAL_SKIPPED, str(self.n_skips),
            self.PAL_NONE, " = ",
            self.PAL_NONE, str(self.n_success + self.n_errors + self.n_skips),
        )
        y += 1
        return y

    def draw_fail_lines(self, y):
        self.result_by_shortcut_number = {}
        if self.n_errors > 0:
            self.print(
                y, 0,
                self.PAL_NONE, f"Failed tests:"
            )
            y += 1

            error_i = 0
            with run_lock:
                for i, (name, result) in enumerate(self.results.items()):
                    if result is not None and result.error is not None:
                        error_i += 1
                        if error_i < 10:
                            self.result_by_shortcut_number[error_i] = result
                            formatted = result.error_formatted
                            lines = []
                            for line in formatted:
                                lines += [sub_line for sub_line in line.strip().split("\n")]
                            last_filename_line = ""
                            if len(lines) >= 3:
                                last_filename_line = lines[-3]
                            split_line = self._traceback_match_filename(last_filename_line)
                            if split_line:
                                leading, basename, lineno, context, is_libs = split_line

                                selected = self.show_result_full_name is not None and self.show_result_full_name == name
                                self.print(
                                    y, 0,
                                    self.PAL_FAIL_KEY, str(error_i),
                                    self.PAL_NONE, " ",
                                    self.PAL_NAME_SELECTED if selected else self.PAL_NAME, name,
                                    self.PAL_ERROR_BASE, " raised: ", self.PAL_ERROR_MESSAGE, result.error.__class__.__name__,
                                    self.PAL_ERROR_BASE, " ",
                                    self.PAL_ERROR_PATHNAME, basename,
                                    self.PAL_ERROR_BASE, ":",
                                    self.PAL_ERROR_PATHNAME, str(lineno),
                                )
                                y += 1

            if self.n_errors > 9:
                self.print(y, 0, self.PAL_ERROR_BASE, f"+ {self.n_errors - 9} more")
                y += 1
        return y

    def draw_result_details(self, y):
        with run_lock:
            result = self.results.get(self.show_result_full_name)

        if result is None:
            return y

        if self.run_state == self.WATCHING:
            _, length = self.print(
                y, 0,
                self.PAL_MENU, "Watching: ",
                self.PAL_MENU_RUN_STATUS, self.watch_file
            )
            self.draw_menu_fill_to_end_of_line(y, length)
            y += 1
        elif self.show_result_full_name is not None:
            _, length = self.print(
                y, 0,
                self.PAL_MENU, "Test result: ",
                self.PAL_MENU_RUN_STATUS, self.show_result_full_name
            )
            self.draw_menu_fill_to_end_of_line(y, length)
            y += 1

        _, length = self.print(
            y, 0,
            self.PAL_MENU_KEY, "r",
            self.PAL_MENU, "e-run this test   ",
            self.PAL_MENU_KEY, "w",
            self.PAL_MENU, "atch test file (auto-re-run)   ",
        )
        self.draw_menu_fill_to_end_of_line(y, length)
        y += 1

        if result.is_running is True:
            self.print(y, 0, self.PAL_NONE, "Runnning...")
            y += 1
        elif result.error is None:
            self.print(y, 0, self.PAL_SUCCESS, "Passed!")
            y += 1
        else:
            formatted = result.error_formatted
            lines = []
            for line in formatted:
                lines += [sub_line for sub_line in line.strip().split("\n")]

            is_libs = False
            for line in lines[1:-1]:
                s = []
                split_line = self._traceback_match_filename(line)
                if split_line is None:
                    s += [self.PAL_ERROR_LIB if is_libs else self.PAL_ERROR_BASE, line]
                else:
                    leading, basename, lineno, context, is_libs = split_line
                    if is_libs:
                        s += [self.PAL_ERROR_LIB, "File ", leading, "/", basename]
                        s += [self.PAL_ERROR_LIB, ":", str(lineno)]
                        s += [self.PAL_ERROR_LIB, " in function "]
                        s += [self.PAL_ERROR_LIB, context]
                    else:
                        s += [
                            self.PAL_ERROR_BASE, "File ",
                            self.PAL_ERROR_PATHNAME, leading,
                            self.PAL_ERROR_BASE, "/ ",
                            self.PAL_ERROR_FILENAME, basename,
                            self.PAL_ERROR_BASE, ":",
                            self.PAL_ERROR_LINENO, str(lineno),
                            self.PAL_ERROR_BASE, " in function ",
                        ]
                        s += [self.PAL_ERROR_MESSAGE, context]
                self.print(y, 0, *s)
                y += 1

            s = [self.PAL_ERROR_BASE, "raised: ", self.PAL_ERROR_MESSAGE, result.error.__class__.__name__]
            self.print(y, 0, *s)
            y += 1

            error_message = str(result.error).strip()
            if error_message != "":
                self.print(y, 4, self.PAL_ERROR_MESSAGE, error_message)

            if result.stdout is not None and result.stdout != "":
                y += 1
                y, _ = self.print(y, 0, self.PAL_NONE, "Stdout:")
                y, _ = self.print(y, 0, self.PAL_STDOUT, result.stdout)
                y += 1

            if result.stderr is not None and result.stderr != "":
                y += 1
                y, _ = self.print(y, 0, self.PAL_NONE, "Stderr:")
                y, _ = self.print(y, 0, self.PAL_STDOUT, result.stderr)
                y += 1

        return y

    def runner_thread_fn(self, allow_to_run, match_string):
        log("enter runner_thread")
        try:
            log(f"runner_thread_fn. match_string={match_string}")
            super().run(allow_to_run=allow_to_run, match_string=match_string)
        except BaseException as e:
            log(f"runner_thread exception {type(e)} {e}")
        finally:
            log("exit runner_thread")

    def runner_thread_start(self):
        if self.runner_thread_is_running():
            raise Exception("Runner thread already running")

        self.n_success = 0
        self.n_errors = 0
        self.n_skips = 0
        self.complete = False
        self.request_stop = False
        assert self.runner_thread is None
        # Why daemonize? Because a daemon thread can not prevent the program from
        # terminating. We do not want the testing thread to be able to prevent
        # the tester application to terminating.
        log(f"starting... {self.match_string}")
        self.runner_thread = threading.Thread(target=self.runner_thread_fn, daemon=True, args=(
            copy.copy(self.allow_to_run),
            copy.copy(self.match_string),
        ))
        self.runner_thread.name = "runner_thread"
        self.runner_thread.start()
        log(f"self.runner_thread_start native_id={self.runner_thread.native_id} ident={self.runner_thread.ident}")

    def runner_thread_is_running(self):
        return self.runner_thread is not None and self.runner_thread.is_alive()

    def render(self):
        if not self.dirty:
            return
        self.dirty = False
        self.scr.clear()
        y = self.draw_title_bar()
        y = self.draw_status(y)
        y = self.draw_summary(y)
        self.draw_fail_lines(y+1)
        y = self.draw_result_details(y+13)
        self.scr.refresh()

    def num_key_to_int(self, key):
        return ord(key) - ord("0")

    def update_run_state(self):
        """
        This is the state machine that is called by the main ui thread "zest_ui_thread".
        All transitions of state are made here. Other code can set the "self.request_*" properties
        but the state only changes here.

        Understanding the process and threads
            * When started in UI mode, the main thread is running curses and is named "zest_ui_thread".
            * It is this main "zest_ui_thread" this is calling this state function.

            RUNNING TESTS
            * When in NO_RUN state a self.request_run can trigger a new run.
            * New runs launch a new thread with runner_thread_start() that is called "runner_thread"
            * The runner_thread is done when the self.run() returns or exceptions
            * The runner_thread catches all exceptions and therefore should not exception itself.
            * The self.run() is very complex and can launch sub-processes which themselves can
              launch sub-processes, etc. It begins by searching for the root zests
              (functions that start with zest_) and then calls _launch_root_zests()

            _launch_root_zests()
            * If n_workers==1 then it bypasses all multi-processing complexity and
              calls the zest.do directly for each zest FROM the "runner_thread"
            * If n_workers>1 then it gets complicated

            MULTI-PROCESS TEST RUNNING from _launch_root_zests():
            * If n_workers>1 then a NonDaemonicPool process pool is created.
            * This MUST BE a NonDaemonicPool because a DaemonicPool (the default Pool)
              is not allowed to create child processes... but creating child processes
              is something that tests regually do, so we have to be NonDaemonic BUT this
              comes with some complexity.
            * A Non-Daemonic Process (ie any test) can PREVENT the parent process
              (the parent of "runner_thread") from terminating if it is not done.
              Thus, if user presses "q" the UI will not want to termiante because it
              is awaiting the NonDaemonic test processes to terminate.
            [TO DO DOENST SEEM RIGHT ABOVE]

            * _launch_root_zests creates the NonDaemonicPool and async_applies
              all of the root tests so that they are all ready to be executed
              and then goes into a loop reading from an inter-process queue listening to
              events from the pool of tests.
              This loop is running in the same "runner_thread"
            * That pool-queue-reading operation (again, in the "runner_thread")
              checks to see if the request_stop flag has been set
              (which could be set by the self.run_state == self.STOPPING
              logic in this function). If it is set then it calls "pool.terminate()"

            TODO: Finish

            Check state for corruptions
            Check for exit cases
            Clear state
            Set new state
        """

        def new_state(state):
            log(f"state change. old_state:{self.run_state_strs[self.run_state]} new_state:{self.run_state_strs[state]}")
            self.run_state = state
            self.dirty = True

        def join_runner_thread():
            self.runner_thread.join()
            self.runner_thread = None

        if self.run_state == self.STOPPED:
            # Tests are done. The runner_thread should be stopped
            # Ways out:
            #    * request_end can terminate
            #    * request_run can start a new run
            assert self.runner_thread is None
            assert self.pool is None

            if self.request_end:
                return False

            if self.request_run is not None:
                self.results = {}
                self.allow_to_run = [self.request_run]
                self.runner_thread_start()
                self.request_run = None
                new_state(self.RUNNING)

        elif self.run_state == self.RUNNING:
            # Tests are running.
            # Ways out:
            #    * request_end: Goto STOPPING
            #    * the "runner_thread" has terminated. Goto STOPPED
            #    * a new run is requested before the current run has terminated. Goto STOPPING
            if not self.runner_thread_is_running():
                join_runner_thread()
                new_state(self.STOPPED)

            elif self.request_end:
                self.request_stop = True
                new_state(self.STOPPING)

            elif self.request_run is not None:
                # Request of a new run, need to stop the previous one first
                self.request_stop = True
                new_state(self.STOPPING)

        elif self.run_state == self.STOPPING:
            # Trying to stop.
            # Ways out:
            #   * The "runner_thread" has terminated. Goto STOPPED
            self.request_stop = True
            if not self.runner_thread_is_running():
                join_runner_thread()
                new_state(self.STOPPED)

        # elif self.run_state == self.WATCHING:
        #     if self.watch_timestamp != os.path.getmtime(self.watch_file):
        #         self.request_run = ".".join(self.request_watch[2])
        #     if self.request_run is not None:
        #         self.run_state = self.STOPPED
        #         self.watch_timestamp = None
        #         self.watch_file = None
        #         self.request_watch = None
        #         self.dirty = True

        return True  # True means keep running

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.show_result_full_name = None
        self.request_run = None
        self.request_stop = False
        self.scr = None
        self.runner_thread = None
        self.current_running_tests_by_pid = None
        self.n_success = None
        self.n_errors = None
        self.n_skips = None
        self.complete = None
        self.key = None
        self.num_keys = [str(i) for i in range(1, 10)]
        self.run_state = None
        self.watch_file = None
        self.watch_timestamp = None
        self.result_by_shortcut_number = None
        self.verbose = 0

    def run(self):
        log("enter ZestConsoleUI")
        threading.current_thread().name = "zest_ui_thread"
        curses.wrapper(self._run)
        log("exit ZestConsoleUI")
        return self

    def _run(self, scr):
        self.scr = scr
        self.runner_thread = None
        self.dirty = False
        self.current_running_tests_by_pid = {}
        self.results = {}
        self.n_success = 0
        self.n_errors = 0
        self.n_skips = 0
        self.complete = False
        self.key = None
        self.show_result_full_name = None
        self.run_state = self.STOPPED
        self.watch_file = None
        self.watch_timestamp = None

        self.request_run = "__all__"
        self.request_watch = None
        self.request_stop = False
        self.request_end = False

        curses.use_default_colors()
        for i, p in enumerate(self.pal):
            if i > 0:
                curses.init_pair(i, self.pal[i][0], self.pal[i][1])

        while True:
            try:
                if not self.update_run_state():
                    break

                self.render()
                if kbhit():
                    key = self.scr.getkey()
                    if key in self.num_keys:
                        show_details_i = self.num_key_to_int(key)
                        if 1 <= show_details_i < self.n_errors + 1:
                            with run_lock:
                                if self.result_by_shortcut_number is not None:
                                    result = self.result_by_shortcut_number.get(show_details_i)
                                    if result is not None:
                                        if self.show_result_full_name == result.full_name:
                                            # Already showing, hide it
                                            self.show_result_full_name = None
                                        else:
                                            self.show_result_full_name = result.full_name
                                        self.dirty = True

                    if key == "q":
                        self.request_end = True

                    if key == "a":
                        self.request_run = "__all__"
                        self.dirty = True

                    if key == "f":
                        self.request_run = "__failed__"
                        self.dirty = True

                    if key == "r":
                        self.request_run = self.show_result_full_name
                        self.dirty = True

                    if key == "w":
                        if self.show_result_full_name is not None:
                            self.request_watch = self.show_result_full_name
                            self.dirty = True

                time.sleep(0.05)

            except KeyboardInterrupt:
                self.request_stop = True
