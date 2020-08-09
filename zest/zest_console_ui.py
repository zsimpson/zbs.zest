import time
import sys
import os
import curses
import threading
from zest.zest_runner import log, ZestRunner
from dataclasses import dataclass
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


@dataclass
class ZestResult:
    error: Exception
    error_formatted: str
    elapsed: float
    skip: str
    shortcut_key: int
    running: bool


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

    def event_test_start(self, call_stack, skip, source, pid):
        """
        This is a callback in the runner thread
        """
        self.dirty = True
        self.current_running_tests_by_pid[pid] = " . ".join(call_stack)

    def event_test_stop(self, call_stack, error, error_formatted, elapsed, skip, source, pid):
        """
        This is a callback in the runner thread
        """
        self.dirty = True
        self.current_running_tests_by_pid[pid] = None
        with run_lock:
            self.results[".".join(call_stack)] = ZestResult(error, error_formatted, elapsed, skip, None, False)
        if error is not None:
            self.n_errors += 1
        else:
            self.n_success += 1

    def print(self, y, x, *args):
        pal = self.PAL_MENU
        for arg in args:
            if isinstance(arg, int):
                pal = arg
            else:
                self.scr.addstr(y, x, str(arg), self.pal[pal][2] | curses.color_pair(pal))
                x += len(str(arg))
        return x

    def draw_menu_fill_to_end_of_line(self, y, length):
        rows, cols = self.scr.getmaxyx()
        if cols - length > 0:
            self.scr.addstr(y, length, f"{' ':<{cols - length}}", curses.color_pair(self.PAL_MENU))

    def draw_title_bar(self):
        y = 0
        length = self.print(
            0, 0,
            self.PAL_MENU_TITLE, f"Zest-Runner v{__version__}  ",
            self.PAL_MENU_KEY, "q)",
            self.PAL_MENU, "uit   ",
            self.PAL_MENU, "run ",
            self.PAL_MENU_KEY, "a)",
            self.PAL_MENU, "ll   ",
            self.PAL_MENU, "run ",
            self.PAL_MENU_KEY, "f)",
            self.PAL_MENU, "ails   ",
        )
        self.draw_menu_fill_to_end_of_line(0, length)
        y += 1
        return y

    def draw_status(self, y):
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
        if self.n_errors > 0:
            self.print(
                y, 0,
                self.PAL_NONE, f"Failed tests:"
            )
            y += 1

            error_i = 0
            with run_lock:
                for i, (name, result) in enumerate(self.results.items()):
                    if result.error is not None:
                        error_i += 1
                        if error_i < 10:
                            result.shortcut_key = error_i
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

                                selected = self.show_result is not None and self.show_result == name
                                self.print(
                                    y, 0,
                                    self.PAL_FAIL_KEY, str(error_i),
                                    self.PAL_NONE, ") ",
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
            result = self.results.get(self.show_result)
        if result is None:
            return y

        if self.run_state == self.WATCHING:
            length = self.print(
                y, 0,
                self.PAL_MENU, "Watching: ",
                self.PAL_MENU_RUN_STATUS, self.watch_file
            )
            self.draw_menu_fill_to_end_of_line(y, length)
            y += 1
        elif self.show_result is not None:
            length = self.print(
                y, 0,
                self.PAL_MENU, "Test result: ",
                self.PAL_MENU_RUN_STATUS, self.show_result
            )
            self.draw_menu_fill_to_end_of_line(y, length)
            y += 1

        length = self.print(
            y, 0,
            self.PAL_MENU_KEY, "r)",
            self.PAL_MENU, "e-run this test   ",
            self.PAL_MENU_KEY, "w)",
            self.PAL_MENU, "atch test file (auto-re-run)   ",
        )
        self.draw_menu_fill_to_end_of_line(y, length)
        y += 1

        if result.running is True:
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

        return y

    def runner_thread_fn(self, which, run_list):
        log("enter runner_thread")
        try:
            kwargs = self.kwargs
            kwargs["match_string"] = which
            kwargs["run_list"] = run_list
            self.run(**kwargs)
        except BaseException as e:
            log(f"runner_thread exception {type(e)} {e}")
        finally:
            log("exit runner_thread")

    def runner_thread_start(self, which):
        if self.runner_thread_is_running():
            raise Exception("Runner thread already running")

        run_list = None

        if which == "*":
            which = None

        if which == "__fails__":
            with run_lock:
                run_list = [name for name, result in self.results.items() if result.error is not None]

        self.n_success = 0
        self.n_errors = 0
        self.n_skips = 0
        self.complete = False
        self.request_stop = False
        assert self.runner_thread is None
        # Why daemonize? Because a daemon thread can not prevent the program from
        # terminating. We do not want the testing thread to be able to prevent
        # the tester application to terminating.
        self.runner_thread = threading.Thread(target=self.runner_thread_fn, args=(which, run_list), daemon=True)
        self.runner_thread.name = "runner_thread"
        self.runner_thread.start()
        log(f"self.runner_thread native_id={self.runner_thread.native_id} ident={self.runner_thread.ident}")

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
        if self.show_result is not None:
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
                self.runner_thread_start(which=self.request_run)
                with run_lock:
                    self.request_run = None
                    self.results = {}
                    # if self.request_run == "*":
                    #     self.show_result = None
                    # else:
                    #     self.results[self.request_run] = ZestResult(None, None, None, None, None, True)
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
        log("enter ZestConsoleUI", str(kwargs))
        super().__init__(**kwargs)
        threading.current_thread().name = "zest_ui_thread"
        curses.wrapper(self.start, **kwargs)
        log("exit ZestConsoleUI")

    def start(self, scr, **kwargs):
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
        self.num_keys = [str(i) for i in range(1, 10)]
        self.show_result = None
        self.run_state = self.STOPPED
        self.watch_file = None
        self.watch_timestamp = None
        self.kwargs = kwargs

        # match_string is nt the same thing as a test
        # We can't set that here
        #self.request_run = kwargs.pop("match_string", "*")
        #if self.request_run is None:
        #    self.request_run = "*"
        # HACK: Temporarily run just the one test: zest_slow_simple_test
        #self.request_run = "zest_slow_simple_test"
        self.request_run = "*"
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
                                for name, result in self.results.items():
                                    if result.shortcut_key == show_details_i:
                                        self.show_result = name
                                        break
                                self.dirty = True

                    if key == "q":
                        self.request_end = True

                    if key == "a":
                        self.request_run = "*"
                        self.dirty = True

                    if key == "f":
                        self.request_run = "__fails__"
                        self.dirty = True

                    if key == "r":
                        self.request_run = self.show_result
                        self.dirty = True

                    if key == "w":
                        if self.show_result is not None:
                            self.request_watch = self.show_result
                            self.dirty = True

                time.sleep(0.05)

            except KeyboardInterrupt:
                self.request_stop = True
