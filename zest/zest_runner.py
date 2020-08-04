import threading
import time
import argparse
import ast
import os
import pkgutil
import re
import sys
import traceback
import curses
import copy
from pathlib import Path
from typing import Callable
from importlib import import_module, util
from dataclasses import dataclass
from . import __version__
if os.name == 'nt':
    import msvcrt
else:
    import select

from zest import zest


blue = "\u001b[34m"
yellow = "\u001b[33m"
red = "\u001b[31m"
green = "\u001b[32m"
gray = "\u001b[30;1m"
cyan = "\u001b[36m"
magenta = "\u001b[35m"
bold = "\u001b[1m"
reset = "\u001b[0m"

_tty_size_cache = None


def tty_size():
    global _tty_size_cache
    if _tty_size_cache is None:
        rows, cols = os.popen("stty size", "r").read().split()
        _tty_size_cache = (int(rows), int(cols))
    return _tty_size_cache


log_fp = None
def log(*args):
    global log_fp
    if log_fp is None:
        log_fp = open("log.txt", "a")
    log_fp.write("".join([str(i) for i in args]) + "\n")
    log_fp.flush()


class ZestRunner:
    """
    The stand-alone zest runner.

    Searches the cwd for zests/ folders and adds and top-level zest_* function
    that it finds in any module in those folders.

    Expects to the cwd to be the root of your project. This is important
    because the import_module parses the path to inform the package argument.
    """

    n_zest_missing_errors = 0

    tb_pat = re.compile(r"^.*File \"([^\"]+)\", line (\d+), in (.*)")

    def s(self, *strs):
        return sys.stdout.write("".join(strs) + reset)

    def _traceback_match_filename(self, line):
        m = self.tb_pat.match(line)
        if m:
            file = m.group(1)
            lineno = m.group(2)
            context = m.group(3)
            file = os.path.relpath(os.path.realpath(file))

            is_libs = True
            real_path = os.path.realpath(file)
            if real_path.startswith(self.root) and os.path.exists(real_path):
                is_libs = False

            if "/site-packages/" in file:
                # Treat these long but commonly occurring path differently
                file = re.sub(r".*/site-packages/", ".../", file)
            leading, basename = os.path.split(file)
            leading = f"{'./' if len(leading) > 0 and leading[0] != '.' else ''}{leading}"
            return leading, basename, lineno, context, is_libs
        return None

    def display_start(self, name, curr_depth, func):
        """Overload this to change output behavior"""
        if self.last_stack_depth < curr_depth:
            self.s("\n")
        self.last_stack_depth = curr_depth
        marker = "+" if self.add_markers else ""
        self.s("  " * curr_depth, yellow, marker + name, reset, ": ")
        # Note, no \n on this line because it will be added on the display_stop call

    def display_stop(self, name, error, curr_depth, last_depth, elapsed, func):
        """Overload this to change output behavior"""
        if curr_depth < last_depth:
            self.s(f"{'  ' * curr_depth}")

        if isinstance(error, str) and error.startswith("skipped"):
            self.s(bold, yellow, error)
        elif hasattr(func, "skip"):
            self.s(bold, yellow, "SKIPPED ", getattr(func, "skip_reason", "") or "")
        elif error:
            self.s(bold, red, "ERROR", gray, f" (in {int(1000.0 * elapsed)} ms)")
        else:
            self.s(green, "SUCCESS", gray, f" (in {int(1000.0 * elapsed)} ms)")
        self.s("\n")

    def display_abbreviated(self, name, error, func):
        """Overload this to change output behavior"""
        if error:
            self.s(bold, red, "F")
        elif hasattr(func, "skip"):
            self.s(yellow, "s")
        else:
            self.s(green, ".")

    def display_warnings(self, call_warnings):
        for warn in call_warnings:
            self.s(yellow, warn, "\n")

    def error_header(self, edge, edge_style, label):
        return (
            edge_style
            + (edge * 5)
            + " "
            + label
            + " "
            + reset
            + edge_style
            + (edge * (tty_size()[1] - 7 - len(label)))
        )

    def display_error(self, error, stack):
        leaf_test_name = stack[-1]
        formatted_test_name = (
            " . ".join(stack[0:-1]) + bold + " . " + leaf_test_name
        )

        self.s("\n", self.error_header("=", red, formatted_test_name), "\n")
        formatted = traceback.format_exception(
            etype=type(error), value=error, tb=error.__traceback__
        )
        lines = []
        for line in formatted:
            lines += [sub_line for sub_line in line.strip().split("\n")]

        is_libs = False
        for line in lines[1:-1]:
            split_line = self._traceback_match_filename(line)
            if split_line is None:
                self.s(gray if is_libs else "", line, "\n")
            else:
                leading, basename, lineno, context, is_libs = split_line
                if is_libs:
                    self.s(gray, "File ", leading, "/", basename)
                    self.s(gray, ":", lineno)
                    self.s(gray, " in function ")
                    self.s(gray, context, "\n")
                else:
                    self.s("File ", yellow, leading, "/", yellow, bold, basename)
                    self.s(":", yellow, lineno)
                    self.s(" in function ")
                    if leaf_test_name == context:
                        self.s(red, bold, context, "\n")
                    else:
                        self.s(magenta, bold, context, "\n")

        self.s(red, "raised: ", red, bold, error.__class__.__name__, "\n")
        error_message = str(error).strip()
        if error_message != "":
            self.s(red, error_message, "\n")

    def display_errors(self, call_log, call_errors):
        self.s("\n")
        for error, stack in call_errors:
            self.display_error(error, stack)

    def display_complete(self, call_log, call_errors):
        n_errors = len(call_errors)
        self.s(f"\nRan {len(call_log)} tests. ")
        if n_errors == 0:
            self.s(green, "SUCCESS\n")
        else:
            self.s(red, bold, f"{n_errors} ERROR(s)\n")

    def walk(self):
        for folder in self.include_dirs:
            for curr, dirs, files in os.walk(
                os.path.abspath(os.path.join(self.root, folder))
            ):
                dirs[:] = [d for d in dirs if d[0] != "."]
                if curr.endswith("/zests"):
                    yield curr

    def recurse_ast(self, func_body, parent_name, skips, path, lineno):
        """
        Args:
            func_body: the AST func_body or module body
            parent_name: The parent function name or None if a module
            skips: a list of skip names for the current func_body or None if module

        Returns:
            list of children: [(full_name, _groups, skips)]

        """
        n_test_funcs = 0
        found_zest_call = False
        found_zest_call_before_final_func_def = False
        child_list = []
        for i, part in enumerate(func_body):
            if isinstance(part, ast.With):
                child_list += self.recurse_ast(part.body, parent_name, skips, path, part.lineno)

            if isinstance(part, ast.FunctionDef):
                full_name = None
                if parent_name is None:
                    if part.name.startswith("zest_"):
                        full_name = f"{part.name}"
                else:
                    if not part.name.startswith("_"):
                        full_name = f"{parent_name}.{part.name}"

                _groups = []
                _skips = []
                if part.decorator_list:
                    for dec in part.decorator_list:
                        if isinstance(dec, ast.Call):
                            if isinstance(dec.func, ast.Attribute):
                                if dec.func.attr == "group":
                                    if isinstance(dec.args[0], ast.Str):
                                        _groups += [dec.args[0].s]
                                elif dec.func.attr == "skip":
                                    if len(dec.args) > 0 and isinstance(
                                        dec.args[0], ast.Str
                                    ):
                                        _skips += [dec.args[0].s]
                                    elif len(dec.keywords) > 0 and isinstance(
                                        dec.keywords[0].value, ast.Str
                                    ):
                                        _skips += [dec.keywords[0].value.s]

                if full_name is not None:
                    child_list += [(full_name, set(_groups), _skips)]
                    n_test_funcs += 1
                    child_list += self.recurse_ast(part.body, full_name, _skips, path, part.lineno)

                if found_zest_call:
                    found_zest_call_before_final_func_def = True

            if isinstance(part, ast.Expr):
                if isinstance(part.value, ast.Call):
                    if isinstance(part.value.func, ast.Name):
                        if part.value.func.id == "zest":
                            found_zest_call = True

        if (
            n_test_funcs > 0
            and parent_name is not None
            and not found_zest_call
            and not skips
        ):
            ZestRunner.n_zest_missing_errors += 1
            common_wording = "If you are using local functions that are not tests, prefix them with underscore."
            if found_zest_call_before_final_func_def:
                self.s(
                    red, "\nERROR: ",
                    reset,
                    "Zest function '",
                    bold, red,
                    parent_name,
                    reset,
                    f" (@ {path}:{lineno}) ",
                    f"' did not call zest() before all functions were defined. {common_wording}\n",
                )
            else:
                self.s(
                    red, "\nERROR: ",
                    reset,
                    "Zest function '",
                    bold, red,
                    parent_name,
                    reset,
                    f" (@ {path}:{lineno}) ",
                    f"' did not terminate with a call to zest(). {common_wording}\n",
                )

        return child_list

    def event_test_start(self, name, call_stack, func):
        """Track the callback depth and forward to the display_start()"""
        if self.verbose >= 2:
            self.callback_depth = len(call_stack) - 1
            self.display_start(name, self.callback_depth, func)

    def event_test_stop(self, name, call_stack, error, elapsed, func):
        """
        Track the callback depth and forward to the
        display_stop() or display_abbreviated()
        """
        log(".".join(call_stack))
        self.timings += [(name, elapsed)]
        self.timings += [(name, elapsed)]
        if self.verbose >= 2:
            curr_depth = len(call_stack) - 1
            self.display_stop(
                name, error, curr_depth, self.callback_depth, elapsed, func
            )
            self.callback_depth = curr_depth
        elif self.verbose == 1:
            self.display_abbreviated(name, error, func)

    def event_considering(self, root_name, module_name, package, member_groups):
        if self.verbose > 2:
            marker = "?" if self.add_markers else ""
            self.s(
                cyan,
                marker + root_name,
                gray,
                f" module_name={module_name}, package={package}, member_groups={member_groups}: ",
            )

    def event_skip(self, root_name):
        if self.verbose > 2:
            self.s(cyan, f"Skipping\n")

    def event_running(self, root_name):
        if self.verbose > 2:
            self.s(cyan, f"Running\n")

    def event_not_running(self, root_name):
        if self.verbose > 2:
            self.s(cyan, f"Not running\n")

    def event_stop_requested(self):
        return False

    def event_complete(self):
        self.display_errors(zest._call_log, zest._call_errors)
        self.display_complete(zest._call_log, zest._call_errors)
        if self.verbose > 1:
            self.s("Slowest 5%\n")
            n_timings = len(self.timings)
            self.timings.sort(key=lambda tup: tup[1])
            ninty_percentile = 95 * n_timings // 100
            for i in range(n_timings - 1, ninty_percentile, -1):
                name = self.timings[i]
                self.s("  ", name[0], gray, f" {int(1000.0 * name[1])} ms)\n")

        self.display_warnings(zest._call_warnings)

    '''
    def _do_work_orders():
        n_work_orders

        # TODO: Change 2 to n_cpus
        with ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="zest_runner"
        ) as executor:
            try:
                wo_i_by_future = {}
                for i, work_order in enumerate(self.work_orders):
                    future = executor.submit(_run_work_order_fn, i)
                    wo_i_by_future[future] = i

                self.results = [None] * zap.n_work_orders

                for future in as_completed(wo_i_by_future):
                    i = wo_i_by_future[future]
                    work_order = zap.work_orders[i]
                    result, duration = future.result()
                    results[i] = _examine_result(zap, result, work_order)

                return results, timings

            except BaseException as e:
                # Any sort of exception needs to clear all threads.
                # Note that KeyboardInterrupt inherits from BaseException not
                # Exception so using BaseException to include KeyboardInterrupts
                # Unlike above with os.kill(), the thread clears are not so destructive,
                # so we want to call them in any situation in which we're bubbling up the
                # exception.
                executor._threads.clear()
                thread._threads_queues.clear()
                raise e
    '''

    def run(
        self,
        root=None,
        verbose=1,
        include_dirs=None,
        match_string=None,
        recurse=0,
        disable_shuffle=False,
        add_markers=False,
        run_list=None,
    ):
        """
        verbose=0 if you want no output
        verbose=1 if you want normal output (with dots)
        verbose=2 if you want full output (with names)

        groups:
            None runs all
            colon-delimited list to run certain kinds
            "unit" is assumed on all un-grouped tests.
        """
        zest.reset()
        zest._disable_shuffle = disable_shuffle

        self.verbose = verbose
        self.callback_depth = 0
        self.include_dirs = (include_dirs or "").split(":")
        self.timings = []
        self.last_stack_depth = 0
        self.add_markers = add_markers

        # zest runner must start in the root of the project
        # so that modules may be loaded appropriately.
        self.root = root or os.getcwd()
        assert self.root[0] == os.sep
        n_root_parts = len(self.root.split(os.sep))

        allow_to_run = []
        root_zest_funcs = {}
        for curr in self.walk():
            if self.event_stop_requested():
                break

            for _, module_name, _ in pkgutil.iter_modules(path=[curr]):
                path = os.path.join(curr, module_name + ".py")
                with open(path) as f:
                    source = f.read()

                module_ast = ast.parse(source)
                zests = self.recurse_ast(module_ast.body, None, None, path, 0)

                for full_name, member_groups, skips in zests:
                    # If the requested substring is anywhere in the full_name
                    # then we add all the parents
                    # eg:
                    #  full_name = "zest_test1.it_does_y.it_does_y1"
                    #  match_string = "it_does_y1"
                    #  Then allow_to_run == [
                    #    "zest_test1"
                    #    "zest_test1.it_does_y"
                    #    "zest_test1.it_does_y.it_does_y1"
                    #  ]

                    parts = full_name.split(".")
                    sub_dirs = curr.split(os.sep)
                    sub_dirs = sub_dirs[n_root_parts:]
                    package = ".".join(sub_dirs)

                    if match_string is None or match_string in full_name:
                        for i in range(len(parts)):
                            allow_to_run += [".".join(parts[0 : i + 1])]

                    if len(parts) == 1:
                        root_zest_funcs[parts[0]] = (
                            module_name,
                            package,
                            member_groups,
                            path,
                        )

        if run_list is not None:
            zest._allow_to_run = []
            for run in run_list:
                parts = run.split(".")
                for i in range(len(parts)):
                    zest._allow_to_run += [".".join(parts[0: i + 1])]
        else:
            zest._allow_to_run = list(set(allow_to_run))

        has_run = {}

        for (
            root_name,
            (module_name, package, member_groups, full_name),
        ) in root_zest_funcs.items():
            if self.event_stop_requested():
                break

            self.event_considering(root_name, module_name, package, member_groups)

            if not has_run.get(root_name):
                self.event_running(root_name)

                spec = util.spec_from_file_location(module_name, full_name)
                mod = util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)
                func = getattr(mod, root_name)

                has_run[root_name] = True
                assert len(zest._call_stack) == 0
                zest.do(
                    func,
                    test_start_callback=self.event_test_start,
                    test_stop_callback=self.event_test_stop,
                )
            else:
                self.event_not_running(root_name)

        if recurse == 0:
            self.event_complete()
            self.retcode = (
                0
                if len(zest._call_errors) == 0 and ZestRunner.n_zest_missing_errors == 0 and not self.event_stop_requested()
                else 1
            )

        return self


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
    elapsed: float
    func: Callable
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

    NO_RUN = 0
    RUNNING = 1
    STOPPING = 2
    DONE = 3
    WATCHING = 4
    run_state_strs = [
        "Analyzing",
        "Running",
        "Stopping",
        "Done",
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
    def event_considering(self, root_name, module_name, package, member_groups):
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

    def event_stop_requested(self):
        return self.stop_requested

    def event_test_start(self, name, call_stack, func):
        """
        This is a callback in the runner thread
        """
        self.dirty = True
        self.current_run_test = " . ".join(call_stack)

    def event_test_stop(self, name, call_stack, error, elapsed, func):
        """
        This is a callback in the runner thread
        """
        log(".".join(call_stack), error)
        self.dirty = True
        self.current_run_test = None
        with run_lock:
            self.results[".".join(call_stack)] = ZestResult(error, elapsed, func, None, False)
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
        self.print(
            y, 0,
            self.PAL_NONE, "Status  : ",
            self.PAL_STATUS, self.run_state_strs[self.run_state] + " ",
            self.PAL_NAME_SELECTED, self.current_run_test or "",
            self.PAL_NAME_SELECTED, self.watch_file or ""
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
                            formatted = traceback.format_exception(
                                etype=type(result.error), value=result.error, tb=result.error.__traceback__
                            )
                            lines = []
                            for line in formatted:
                                lines += [sub_line for sub_line in line.strip().split("\n")]
                            last_filename_line = lines[-3]
                            split_line = self._traceback_match_filename(last_filename_line)
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
            formatted = traceback.format_exception(
                etype=type(result.error), value=result.error, tb=result.error.__traceback__
            )
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
        try:
            kwargs = self.kwargs
            kwargs["match_string"] = which
            kwargs["run_list"] = run_list
            self.run(**kwargs)
        finally:
            self.runner_thread = None

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
        self.stop_requested = False
        assert self.runner_thread is None
        self.runner_thread = threading.Thread(target=self.runner_thread_fn, args=(which, run_list))
        self.runner_thread.start()

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
        if self.run_state == self.NO_RUN:
            if self.request_run is not None:
                self.runner_thread_start(which=self.request_run)
                with run_lock:
                    self.results = {}
                    if self.request_run == "*":
                        self.results = {}
                        self.show_result = None
                    else:
                        self.results[self.request_run] = ZestResult(None, None, None, None, True)
                    self.run_state = self.RUNNING
                    self.request_run = None
                    self.dirty = True

        elif self.run_state == self.RUNNING:
            if not self.runner_thread_is_running():
                self.run_state = self.DONE
                self.dirty = True
            elif self.request_run is not None:
                # Request of a new run, need to stop the previous
                self.stop_requested = True
                self.run_state = self.STOPPING
                self.dirty = True

        elif self.run_state == self.STOPPING:
            if not self.runner_thread_is_running():
                self.run_state = self.NO_RUN
                self.dirty = True

        elif self.run_state == self.DONE:
            if self.request_run is not None:
                self.run_state = self.NO_RUN
                self.dirty = True
            if self.request_watch is not None:
                with run_lock:
                    self.watch_file = self.results[self.request_watch].func.__code__.co_filename
                self.watch_timestamp = os.path.getmtime(self.watch_file)
                self.run_state = self.WATCHING
                self.dirty = True

        elif self.run_state == self.WATCHING:
            if self.watch_timestamp != os.path.getmtime(self.watch_file):
                self.request_run = ".".join(self.request_watch[2])
            if self.request_run is not None:
                self.run_state = self.NO_RUN
                self.watch_timestamp = None
                self.watch_file = None
                self.request_watch = None
                self.dirty = True

    def __init__(self, scr, **kwargs):
        self.scr = scr
        self.kwargs = kwargs
        self.runner_thread = None
        self.dirty = False
        self.current_run_test = None
        with run_lock:
            self.results = {}
        self.n_success = 0
        self.n_errors = 0
        self.n_skips = 0
        self.complete = False
        self.key = None
        self.num_keys = [str(i) for i in range(1, 10)]
        self.stop_requested = False
        self.show_result = None
        self.run_state = self.NO_RUN
        self.request_run = "*"
        self.request_watch = None
        self.watch_file = None
        self.watch_timestamp = None

        curses.use_default_colors()
        for i, p in enumerate(self.pal):
            if i > 0:
                curses.init_pair(i, self.pal[i][0], self.pal[i][1])

        while True:
            try:
                self.update_run_state()

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
                        return

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
                self.stop_requested = True


def zest_ui(**kwargs):
    # "/Users/zack/git/zbs.zest/ui_tests"
    curses.wrapper(ZestConsoleUI, **kwargs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose",
        default=1,
        type=int,
        help="0=silent, 1=dot-mode, 2=run-trace 3=full-trace",
    )
    parser.add_argument(
        "--include_dirs",
        nargs="?",
        default=os.getcwd(),
        help="Colon-delimited list of directories to search",
    )
    parser.add_argument(
        "--disable_shuffle",
        action="store_true",
        help="Disable the shuffling of test order",
    )
    parser.add_argument(
        "--add_markers", action="store_true", help="Used for internal debugging"
    )
    parser.add_argument(
        "--version", action="store_true", help="Show version and exit",
    )
    parser.add_argument(
        "match_string", type=str, nargs="?", help="Optional substring to match"
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="console UI",
    )
    kwargs = vars(parser.parse_args())

    if kwargs.pop("ui", False):
        curses.wrapper(ZestConsoleUI)
        sys.exit(0)

    if kwargs.pop("version", None):
        print(__version__)
        sys.exit(0)

    runner = ZestRunner().run(**kwargs)
    sys.exit(runner.retcode)


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

        with open(pidfile, 'w') as f:
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
