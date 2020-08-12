import threading
import time
import ast
import os
import pkgutil
import re
import sys
import traceback
import copy
import io
from typing import Callable
from importlib import import_module, util
import multiprocessing
import multiprocessing.pool
from queue import Empty as QueueEmpty
from zest import zest, ZestResult


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
    log_fp.write(f"pid:{os.getpid():6d} tname:{threading.current_thread().name[0:39]:40s} native:{threading.current_thread().native_id:6d}: ")
    log_fp.write("".join([str(i) + " " for i in args]) + "\n")
    log_fp.flush()


# Non-Daemon process pool adpated from : https://stackoverflow.com/a/53180921
# This is necessary because the Pool must be allowed to create other
# processes within in it (ie, a test could create a process)
class NonDaemonicProcess(multiprocessing.Process):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    @property
    def daemon(self):
        return False

    @daemon.setter
    def daemon(self, value):
        pass


class NonDaemonicContext(type(multiprocessing.get_context())):
    Process = NonDaemonicProcess


class NonDaemonicPool(multiprocessing.pool.Pool):
    def __init__(self, *args, **kwargs):
        kwargs['context'] = NonDaemonicContext()
        super(NonDaemonicPool, self).__init__(*args, **kwargs)


def _load_module(root_name, module_name, full_path):
    # TODO: Add cache here
    spec = util.spec_from_file_location(module_name, full_path)
    mod = util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    a = getattr(mod, root_name)
    return getattr(mod, root_name)


def _do_one_root_zest(root_name, module_name, full_path, allow_to_run, queue):
    """
    This is called to run a root-level zest function in a multiprocessing context.
    queue is used to communicate back to the parent process.

    """
    threading.current_thread().name = f"root_name-{root_name}"
    log(f"enter _do_one_root_zest {root_name}")
    try:
        def event_test_start(zest_result):
            queue.put(("test_start", zest_result))

        def event_test_stop(zest_result):
            queue.put(("test_stop", zest_result))

        root_zest_func = _load_module(root_name, module_name, full_path)

        zest.do(
            root_zest_func,
            test_start_callback=event_test_start,
            test_stop_callback=event_test_stop,
            allow_to_run=allow_to_run,
        )

        queue.put(("root_stop", ZestResult([root_name])))
    except Exception as e:
        print(f"_do_one_root_zest exception {e}")
    finally:
        log(f"exit _do_one_root_zest {root_name}")


class ZestRunner:
    n_zest_missing_errors = 0

    tb_pat = re.compile(r"^.*File \"([^\"]+)\", line (\d+), in (.*)")

    # Display (NO UI, See ZestConsoleUI)
    # -----------------------------------------------------------------------------------
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

    def _error_header(self, edge, edge_style, label):
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

    def display_start(self, name, curr_depth, skip):
        """Overload this to change output behavior"""
        if self.last_stack_depth < curr_depth:
            self.s("\n")
        self.last_stack_depth = curr_depth
        marker = "+" if self.add_markers else ""
        self.s("  " * curr_depth, yellow, marker + name, reset, ": ")
        # Note, no \n on this line because it will be added on the display_stop call

    def display_stop(self, name, error, curr_depth, last_depth, elapsed, skip):
        """Overload this to change output behavior"""
        if curr_depth < last_depth:
            self.s(f"{'  ' * curr_depth}")

        if isinstance(error, str) and error.startswith("skipped"):
            self.s(bold, yellow, error)
        elif skip is not None:
            self.s(bold, yellow, "SKIPPED ", skip)
        elif error:
            self.s(bold, red, "ERROR", gray, f" (in {int(1000.0 * elapsed)} ms)")
        else:
            self.s(green, "SUCCESS", gray, f" (in {int(1000.0 * elapsed)} ms)")
        self.s("\n")

    def display_abbreviated(self, name, error, skip):
        """Overload this to change output behavior"""
        if error:
            self.s(bold, red, "F")
        elif skip:
            self.s(yellow, "s")
        else:
            self.s(green, ".")

    def display_warnings(self, call_warnings):
        for warn in call_warnings:
            self.s(yellow, warn, "\n")

    def display_error(self, error, error_formatted, stack):
        leaf_test_name = stack[-1]
        formatted_test_name = (
            " . ".join(stack[0:-1]) + bold + " . " + leaf_test_name
        )

        self.s("\n", self._error_header("=", red, formatted_test_name), "\n")
        lines = []
        for line in error_formatted:
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
        self.s()

    def display_errors(self, call_log, call_errors):
        self.s("\n")
        for error, error_formatted, stack in call_errors:
            self.display_error(error, error_formatted, stack)

    def display_complete(self, call_log, call_errors):
        n_errors = len(call_errors)
        self.s(f"\nRan {len(call_log)} tests. ")
        if n_errors == 0:
            self.s(green, "SUCCESS\n")
        else:
            self.s(red, bold, f"{n_errors} ERROR(s)\n")

    # Events
    # -----------------------------------------------------------------------------------
    def event_test_start(self, zest_result):
        """Track the callback depth and forward to the display_start()"""
        self.results[zest_result.full_name] = None
        if self.verbose >= 2:
            self.callback_depth = len(zest_result.call_stack) - 1
            self.display_start(zest_result.short_name, self.callback_depth, zest_result.skip)

    def event_test_stop(self, zest_result):
        """
        Track the callback depth and forward to the
        display_stop() or display_abbreviated()
        """
        self.results[zest_result.full_name] = zest_result
        if self.verbose >= 2:
            curr_depth = len(zest_result.call_stack) - 1
            self.display_stop(
                zest_result.short_name, zest_result.error, curr_depth, self.callback_depth, zest_result.elapsed, zest_result.skip
            )
            self.callback_depth = curr_depth
        elif self.verbose == 1:
            self.display_abbreviated(zest_result.short_name, zest_result.error, zest_result.skip)

        if self.verbose > 0:
            if zest_result.stdout is not None:
                print(f"  Stdout: {zest_result.stdout}")
            if zest_result.stderr is not None:
                print(f"  Stderr: {zest_result.stderr}")

    def event_considering(self, root_name, module_name, package):
        if self.verbose > 2:
            marker = "?" if self.add_markers else ""
            self.s(
                cyan,
                marker + root_name,
                gray,
                f" module_name={module_name}, package={package}: ",
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

    def event_request_stop(self):
        # Overloaded in sub-classes to notify of a stop request
        return False

    def event_complete(self):
        if self.verbose > 0:
            self.display_errors(zest._call_log, zest._call_errors)
            self.display_complete(zest._call_log, zest._call_errors)
        if self.verbose > 1:
            self.s("Slowest 5%\n")
            n_timings = len(self.results)
            timings = [(full_name, result.elapsed) for full_name, result in self.results.items()]
            timings.sort(key=lambda tup: tup[1])
            ninety_percentile = 95 * n_timings // 100
            for i in range(n_timings - 1, ninety_percentile, -1):
                name = timings[i]
                self.s("  ", name[0], gray, f" {int(1000.0 * name[1])} ms)\n")

        if self.verbose > 0:
            self.display_warnings(zest._call_warnings)

    # Searching
    # -----------------------------------------------------------------------------------

    def walk(self):
        """
        Generator to walk from self.root though all self.included_dirs
        finding any folder that is called "/zests/"
        """
        for folder in self.include_dirs:
            for curr, dirs, files in os.walk(
                os.path.abspath(os.path.join(self.root, folder))
            ):
                dirs[:] = [d for d in dirs if d[0] != "."]
                if curr.endswith("/zests"):
                    yield curr

    def recurse_ast(self, func_body, parent_name, skips, path, lineno):
        """
        Recursively traverse the Abstract Syntax Tree extracting zests

        Args:
            func_body: the AST func_body or module body
            parent_name: The parent function name or None if a module
            skips: a list of skip names for the current func_body or None if module

        Returns:
            list of children: [(full_name, skips)]
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

                _skips = []
                if part.decorator_list:
                    for dec in part.decorator_list:
                        if isinstance(dec, ast.Call):
                            if isinstance(dec.func, ast.Attribute):
                                if dec.func.attr == "skip":
                                    if len(dec.args) > 0 and isinstance(
                                        dec.args[0], ast.Str
                                    ):
                                        _skips += [dec.args[0].s]
                                    elif len(dec.keywords) > 0 and isinstance(
                                        dec.keywords[0].value, ast.Str
                                    ):
                                        _skips += [dec.keywords[0].value.s]

                if full_name is not None:
                    child_list += [(full_name, _skips)]
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
            and (
                not skips
                or (
                    # self.bypass_skip is only for self-testing.
                    # See: it_warns_if_no_trailing_zest
                    skips and self.bypass_skip is not None and self.bypass_skip in skips
                )
            )
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
                    reset, "'",
                    f" (@ {path}:{lineno})",
                    f" did not call zest() before all functions were defined. {common_wording}\n",
                )
            else:
                self.s(
                    red, "\nERROR: ",
                    reset,
                    "Zest function '",
                    bold, red,
                    parent_name,
                    reset, "'",
                    f" (@ {path}:{lineno})",
                    f" did not terminate with a call to zest(). {common_wording}\n",
                )

        return child_list

    def find_zests(self, allow_to_run=None, match_string=None):
        """
        Traverses the tree looking for /zests/ folders and opens and parses any file found

        Arguments:
            allow_to_run:
                If not None: a list of full test names (dot-delimited) that will be included.
                Plus two specials: "__all__" and "__failed__"
            match_string:
                If not None then any zest full name that *contains* this string will be included.
                Note that match_string only narrows the scope from allow_to_run

        Returns:
            dict of root zests by name -> (module_name, package, path)
            set of full names allowed to run (not all test under a root have to be allowed)

        Note, when a zest is identified all of its ancestor are also be added to the the list.
        Example:
            full_name = "zest_test1.it_does_y.it_does_y1"
            match_string = "it_does_y1"
            Then return_allow_to_run == set(
                "zest_test1",
                "zest_test1.it_does_y",
                "zest_test1.it_does_y.it_does_y1",
            )
        """

        if allow_to_run is None:
            allow_to_run = []

        n_root_parts = len(self.root.split(os.sep))
        if "__failed__" in allow_to_run:
            allow_to_run += [name for name, result in self.results.items() if result.error is not None]

        return_allow_to_run = set()  # Full names (dot delimited) of all tests to run
        root_zest_funcs = {}  # A dict of entrypoints (root zests) -> (module_name, package, path)
        for curr in self.walk():
            for _, module_name, _ in pkgutil.iter_modules(path=[curr]):
                path = os.path.join(curr, module_name + ".py")
                with open(path) as file:
                    source = file.read()

                module_ast = ast.parse(source)
                zests = self.recurse_ast(module_ast.body, None, None, path, 0)

                for full_name, skips in zests:
                    parts = full_name.split(".")
                    package = ".".join(curr.split(os.sep)[n_root_parts:])

                    if "__all__" in allow_to_run or full_name in allow_to_run:
                        if match_string is None or match_string in full_name:
                            # Include this and all ancestors in the list
                            for i in range(len(parts)):
                                name = ".".join(parts[0: i + 1])
                                return_allow_to_run.update({name})

                            root_zest_funcs[parts[0]] = (module_name, package, path)

        return root_zest_funcs, return_allow_to_run

    # Run
    # -----------------------------------------------------------------------------------

    def _launch_root_zests(self, root_zest_funcs, allow_to_run, n_workers):
        if n_workers == 1:
            for (
                root_name,
                (module_name, package, full_path),
            ) in root_zest_funcs.items():
                if self.event_request_stop():
                    break
                root_zest_func = _load_module(root_name, module_name, full_path)
                zest.do(
                    root_zest_func,
                    test_start_callback=self.event_test_start,
                    test_stop_callback=self.event_test_stop,
                    allow_to_run=allow_to_run,
                )
        else:
            self.pool = None
            queue = None
            async_results = []
            n_started = 0
            try:
                self.pool = NonDaemonicPool(n_workers)
                queue = multiprocessing.Manager().Queue()

                for (
                    root_name,
                    (module_name, package, full_path),
                ) in root_zest_funcs.items():
                    self.event_considering(root_name, module_name, package)
                    async_results += [self.pool.apply_async(_do_one_root_zest, (root_name, module_name, full_path, allow_to_run, queue))]
                    n_started += 1

                self.pool.close()

                for p in self.pool._pool:
                    log(f"pool_proc pid:{p.pid}")

                n_root_stop_messages_received = 0
                while n_root_stop_messages_received < n_started:
                    try:
                        msg, zest_result = queue.get(block=True, timeout=0.1)

                        if self.event_request_stop():
                            break

                        if msg == "root_stop":
                            n_root_stop_messages_received += 1

                        elif msg == "test_start":
                            self.event_test_start(zest_result)

                        elif msg == "test_stop":
                            self.event_test_stop(zest_result)
                    except QueueEmpty:
                        pass
            except Exception as e:
                log(f"_launch_root_zests exception {type(e)} {e}")
            finally:
                # CONSUME remainder of queue before we're done to free chlidren
                # See "Joining processes that use queues": https://docs.python.org/3/library/multiprocessing.html#multiprocessing.pool.AsyncResult
                log("_launch_root_zests finally")
                while queue is not None:
                    try:
                        queue.get(block=False)
                    except QueueEmpty:
                        break
                if self.pool:
                    self.pool.terminate()
                    self.pool.join()
                self.pool = None
                # Reap (I'm not sure this is necessary but seems like it might prevent zombies)
                # log("mp reap")
                # for res in async_results:
                #     res.get()
                log("mp done")

    def __init__(
        self,
        root=None,
        include_dirs=None,
        allow_to_run="__all__",
        match_string=None,
        verbose=1,
        disable_shuffle=False,
        n_workers=1,
        add_markers=False,
        bypass_skip=None,
    ):
        """
        root:
            The directory under which should be searched for zests and outside of which
            will be considered "library references" (greayed out in error messages)
        include_dirs:
            The folders (relative to root) that should be included in recursive search
        allow_to_run:
            If not None: A list of full test names (dot-delimited) that will be allowed to run
            Special values:
                __all__: Consider all zests to run
                __failed__: Consider previous failed zests
        match_string:
            If not None: A substring that if found in a zest name will include it
            Note: If allow_to_run includes only a subset of zests then this match_string
            can only further restrict the set. A match_string of None does not further restrict
            the list at all.
        verbose:
            0: no output
            1: normal output (dots notation)
            2: full test output (with names)
            3: debugging traces
        disable_shuffle:
            True: runs zests in consistent order.
            False (default): shuffles zests to root out order dependencies
        n_workers:
            Number of parallel workers. When 1, does not create any child workers
            and is easier to debug.
        add_markers:
            Used for debugging. Ignore.
        bypass_skip:
            Used for debugging. Ignore.
        """
        self.root = root
        self.include_dirs = (include_dirs or "").split(":")
        self.allow_to_run = allow_to_run
        self.match_string = match_string
        self.verbose = verbose
        self.disable_shuffle = disable_shuffle
        self.n_workers = n_workers
        self.add_markers = add_markers
        self.bypass_skip = bypass_skip

        self.callback_depth = None
        self.timings = None
        self.last_stack_depth = None
        self.retcode = None
        self.pool = None
        self.results = {}

    def run(self, **kwargs):
        allow_to_run = kwargs.pop("allow_to_run", self.allow_to_run)
        match_string = kwargs.pop("match_string", self.match_string)

        zest.reset()
        zest._disable_shuffle = self.disable_shuffle
        self.callback_depth = 0
        self.timings = []
        self.last_stack_depth = 0
        self.results = {}

        # zest runner must start in the root of the project
        # so that modules may be loaded appropriately.
        self.root = self.root or os.getcwd()
        assert self.root[0] == os.sep

        root_zests, allow_to_run = self.find_zests(allow_to_run, match_string)
        self._launch_root_zests(root_zests, allow_to_run, n_workers=self.n_workers)

        self.event_complete()
        self.retcode = (
            0
            if len(zest._call_errors) == 0 and ZestRunner.n_zest_missing_errors == 0 and not self.event_request_stop()
            else 1
        )

        return self
