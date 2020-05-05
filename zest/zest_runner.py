import re
import argparse
import pkgutil
import os
import sys
import traceback
import ast
from importlib import import_module
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


def s(*strs):
    return sys.stdout.write("".join(strs) + reset)


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

    def _traceback_match_filename(self, line):
        m = self.tb_pat.match(line)
        if m:
            file = m.group(1)
            lineno = m.group(2)
            context = m.group(3)
            file = os.path.relpath(os.path.realpath(file))

            root = os.getcwd()
            is_libs = True
            real_path = os.path.realpath(file)
            if real_path.startswith(root):
                is_libs = False

            if "/site-packages/" in file:
                # Treat these long but commonly occurring path differently
                file = re.sub(r".*/site-packages/", ".../", file)
            leading, basename = os.path.split(file)
            leading = f"{'./' if leading[0] != '.' else ''}{leading}"
            return leading, basename, lineno, context, is_libs
        return None

    def display_start(self, name, curr_depth, func):
        """Overload this to change output behavior"""
        s(yellow, f"\n{'  ' * curr_depth}{name}")
        s(": ")

    def display_stop(self, name, error, curr_depth, last_depth, elapsed, func):
        """Overload this to change output behavior"""
        if curr_depth < last_depth:
            s(f"\n{'  ' * curr_depth}")

        if isinstance(error, str) and error.startswith("skipped"):
            s(bold, yellow, error)
        elif hasattr(func, "skip"):
            s(bold, yellow, "SKIPPED ", getattr(func, "skip_reason", "") or "")
        elif error:
            s(bold, red, "ERROR")
        else:
            s(green, "SUCCESS", gray, f" (in {int(1000.0 * elapsed)} ms)")

    def display_abbreviated(self, name, error, func):
        """Overload this to change output behavior"""
        if error:
            s(bold, red, "F")
        elif hasattr(func, "skip"):
            skip_code = getattr(func, "skip_code", "s")
            s(yellow, skip_code)
        else:
            s(green, ".")

    def display_warnings(self, call_warnings):
        for warn in call_warnings:
            s(yellow, warn, "\n")

    def display_errors(self, call_log, call_errors):
        def header(edge, edge_style, label):
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

        s("\n")

        for error, stack in call_errors:
            leaf_test_name = stack[-1]
            formatted_test_name = (
                " . ".join(stack[0:-1]) + bold + " . " + leaf_test_name
            )

            s("\n", header("=", red, formatted_test_name), "\n")
            formatted = traceback.format_exception(
                etype=type(error), value=error, tb=error.__traceback__
            )
            lines = []
            for line in formatted:
                lines += [sub_line for sub_line in line.strip().split("\n")]

            for line in lines[1:-1]:
                split_line = self._traceback_match_filename(line)
                if split_line is None:
                    s(gray if is_libs else "", line, "\n")
                else:
                    leading, basename, lineno, context, is_libs = split_line
                    if is_libs:
                        s(gray, "File ", leading, "/", basename)
                        s(gray, ":", lineno)
                        s(gray, " in function ")
                        s(gray, context, "\n")
                    else:
                        s("File ", yellow, leading, "/", yellow, bold, basename)
                        s(":", yellow, lineno)
                        s(" in function ")
                        if leaf_test_name == context:
                            s(red, bold, context, "\n")
                        else:
                            s(magenta, bold, context, "\n")

            s(red, "raised: ", red, bold, error.__class__.__name__, "\n")
            error_message = str(error).strip()
            if error_message != "":
                s(red, error_message, "\n")

    def display_complete(self, call_log, call_errors):
        n_errors = len(call_errors)
        s(f"\nRan {len(call_log)} tests. ")
        if n_errors == 0:
            s(green, "SUCCESS\n")
        else:
            s(red, bold, f"{n_errors} ERROR(s)\n")

    def walk(self):
        for root in self.include_dirs:
            for curr, dirs, files in os.walk(os.path.abspath(root)):
                dirs[:] = [d for d in dirs if d[0] != "."]
                if curr.endswith("/zests"):
                    yield curr

    def _test_start_callback(self, name, call_stack, func):
        """Track the callback depth and forward to the display_start()"""
        if self.verbose >= 2:
            self.callback_depth = len(call_stack) - 1
            self.display_start(name, self.callback_depth, func)

    def _test_stop_callback(self, name, call_stack, error, elapsed, func):
        """
        Track the callback depth and forward to the
        display_stop() or display_abbreviated()
        """
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

    def _recurse_ast(self, func_body, parent_name, skips):
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
                    child_list += self._recurse_ast(part.body, full_name, _skips)

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
            and (not skips or (skips and self.bypass_skip is not None and self.bypass_skip in skips))
        ):
            ZestRunner.n_zest_missing_errors += 1
            if found_zest_call_before_final_func_def:
                s(
                    red,
                    f"ERROR: Zest function {parent_name} did not call zest() before all functions were defined\n",
                )
            else:
                s(
                    red,
                    f"ERROR: Zest function {parent_name} did not terminate with a call to zest()\n",
                )

        return child_list

    def __init__(
        self,
        verbose=1,
        include_dirs=None,
        match_string=None,
        recurse=0,
        run_groups=None,
        skip_groups=None,
        disable_shuffle=False,
        bypass_skip=None,
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

        self.bypass_skip = bypass_skip
        self.verbose = verbose
        self.callback_depth = 0
        self.include_dirs = (include_dirs or "").split(":")
        self.timings = []

        # Execute root zests in group order
        if run_groups is None:
            run_groups = "*"
        run_groups = run_groups.split(":")

        if skip_groups is None:
            skip_groups = ""
        skip_groups = set(skip_groups.split(":"))

        # zest runner must start in the root of the project
        # so that modules may be loaded appropriately.
        root = os.getcwd()

        allow_to_run = []
        root_zest_funcs = {}
        for curr in self.walk():
            for _, module_name, _ in pkgutil.iter_modules(path=[curr]):
                path = os.path.join(curr, module_name + ".py")
                with open(path) as f:
                    source = f.read()

                module_ast = ast.parse(source)
                zests = self._recurse_ast(module_ast.body, None, None)

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
                    sub_dirs = curr[len(root):].split(os.sep)
                    package = ".".join(sub_dirs).lstrip("./")
                    if len(member_groups) == 0:
                        # Default to run non-unit tests second
                        member_groups = set(["unit"])

                    if match_string is None or match_string in full_name:
                        for i in range(len(parts)):
                            allow_to_run += [".".join(parts[0 : i + 1])]

                    if len(parts) == 1:
                        root_zest_funcs[parts[0]] = (
                            module_name,
                            package,
                            member_groups,
                        )

        zest._allow_to_run = list(set(allow_to_run))

        has_run = {}
        for run_group in run_groups:
            if run_group != "*":
                if self.verbose > 1:
                    s(cyan, "\nStarting group ", bold, run_group)

            for (
                root_name,
                (module_name, package, member_groups),
            ) in root_zest_funcs.items():
                is_test_in_a_skpped_group = len(member_groups & skip_groups) > 0

                if is_test_in_a_skpped_group:
                    self._test_start_callback(root_name, [], None)
                    self._test_stop_callback(root_name, [], "skipped because it is in a skipped group", 0.0, None)
                    continue

                if (run_group in member_groups or run_group == "*") and not has_run.get(root_name):
                    imported = import_module("." + module_name, package=package)
                    func = getattr(imported, root_name)

                    has_run[root_name] = True
                    assert len(zest._call_stack) == 0
                    zest.do(
                        func,
                        test_start_callback=self._test_start_callback,
                        test_stop_callback=self._test_stop_callback,
                    )

        if recurse == 0:
            self.display_errors(zest._call_log, zest._call_errors)
            self.display_complete(zest._call_log, zest._call_errors)
            self.retcode = (
                0
                if len(zest._call_errors) == 0 and ZestRunner.n_zest_missing_errors == 0
                else 1
            )

            if self.verbose > 1:
                s("Slowest 5%\n")
                n_timings = len(self.timings)
                self.timings.sort(key=lambda tup: tup[1])
                ninty_percentile = 95 * n_timings // 100
                for i in range(n_timings - 1, ninty_percentile, -1):
                    name = self.timings[i]
                    s("  ", name[0], gray, f" {int(1000.0 * name[1])} ms)\n")

            self.display_warnings(zest._call_warnings)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose", default=1, type=int, help="0=silent, 1=dot-mode, 2=full-trace",
    )
    parser.add_argument(
        "--include_dirs",
        nargs="?",
        default=os.getcwd(),
        help="Colon-delimited list of directories to search",
    )
    parser.add_argument(
        "--run_groups",
        type=str,
        nargs="?",
        help="Run these colon-delimited groups. If not specified, only zests with no group will run",
    )
    parser.add_argument(
        "--skip_groups",
        type=str,
        nargs="?",
        help="Skip these colon-delimited groups.",
    )
    parser.add_argument(
        "--disable_shuffle",
        action="store_true",
        help="Disable the shuffling of test order",
    )
    parser.add_argument(
        "--version", action="store_true", help="Show version and exit",
    )
    parser.add_argument(
        "--bypass_skip", nargs="?", help="Run test even if it was skipped given this skip tag",
    )
    parser.add_argument(
        "match_string", type=str, nargs="?", help="Optional substring to match"
    )
    kwargs = vars(parser.parse_args())

    if kwargs.get("version"):
        from . import __version__

        print(__version__)
        sys.exit(0)
    del kwargs["version"]

    runner = ZestRunner(**kwargs)
    sys.exit(runner.retcode)


if __name__ == "__main__":
    main()
