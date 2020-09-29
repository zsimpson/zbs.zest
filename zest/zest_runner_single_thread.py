"""
Single-threaded runner with abbreviated and verbose display options
"""

import sys
import os
import re
from zest import zest
from zest import zest_finder


_tb_pat = re.compile(r"^.*File \"([^\"]+)\", line (\d+), in (.*)")

# Display
# -----------------------------------------------------------------------------------

blue = "\u001b[34m"
yellow = "\u001b[33m"
red = "\u001b[31m"
green = "\u001b[32m"
gray = "\u001b[30;1m"
cyan = "\u001b[36m"
magenta = "\u001b[35m"
bold = "\u001b[1m"
reset = "\u001b[0m"


def _s(*strs):
    return sys.stdout.write("".join(strs) + reset)


def traceback_match_filename(root, line):
    m = _tb_pat.match(line)
    if m:
        file = m.group(1)
        lineno = m.group(2)
        context = m.group(3)
        file = os.path.relpath(os.path.realpath(file))

        is_libs = True
        real_path = os.path.realpath(file)
        if real_path.startswith(root) and os.path.exists(real_path):
            is_libs = False

        if "/site-packages/" in file:
            # Treat these long but commonly occurring path differently
            file = re.sub(r".*/site-packages/", ".../", file)
        leading, basename = os.path.split(file)
        leading = f"{'./' if len(leading) > 0 and leading[0] != '.' else ''}{leading}"
        return leading, basename, lineno, context, is_libs
    return None


def _error_header(edge, edge_style, label):
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


_tty_size_cache = None


def tty_size():
    global _tty_size_cache
    if _tty_size_cache is None:
        rows, cols = os.popen("stty size", "r").read().split()
        _tty_size_cache = (int(rows), int(cols))
    return _tty_size_cache


# Display functions output messages
# ---------------------------------------------------------------------------------


def _display_start(name, last_depth, curr_depth, add_markers):
    if last_depth < curr_depth:
        _s("\n")
    marker = "+" if add_markers else ""
    _s("  " * curr_depth, yellow, marker + name, reset, ": ")
    # Note, no \n on this line because it will be added on the display_stop call


def _display_stop(error, elapsed, skip, last_depth, curr_depth):
    if curr_depth < last_depth:
        _s(f"{'  ' * curr_depth}")
    if isinstance(error, str) and error.startswith("skipped"):
        _s(bold, yellow, error)
    elif skip is not None:
        _s(bold, yellow, "SKIPPED (reason: ", skip, ")")
    elif error:
        _s(bold, red, "ERROR", gray, f" (in {int(1000.0 * elapsed)} ms)")
    else:
        _s(green, "SUCCESS", gray, f" (in {int(1000.0 * elapsed)} ms)")
    _s("\n")


def _display_abbreviated(error, skip):
    """Overload this to change output behavior"""
    if error:
        _s(bold, red, "F")
    elif skip:
        _s(yellow, "s")
    else:
        _s(green, ".")


def _display_warnings(call_warnings):
    for warn in call_warnings:
        _s(yellow, warn, "\n")


def _display_error(root, error, error_formatted, stack):
    leaf_test_name = stack[-1]
    formatted_test_name = " . ".join(stack[0:-1]) + bold + " . " + leaf_test_name

    _s("\n", _error_header("=", red, formatted_test_name), "\n")
    lines = []
    for line in error_formatted:
        lines += [sub_line for sub_line in line.strip().split("\n")]

    is_libs = False
    for line in lines[1:-1]:
        split_line = traceback_match_filename(root, line)
        if split_line is None:
            _s(gray if is_libs else "", line, "\n")
        else:
            leading, basename, lineno, context, is_libs = split_line
            if is_libs:
                _s(gray, "File ", leading, "/", basename)
                _s(gray, ":", lineno)
                _s(gray, " in function ")
                _s(gray, context, "\n")
            else:
                _s("File ", yellow, leading, "/", yellow, bold, basename)
                _s(":", yellow, lineno)
                _s(" in function ")
                if leaf_test_name == context:
                    _s(red, bold, context, "\n")
                else:
                    _s(magenta, bold, context, "\n")

    _s(red, "raised: ", red, bold, error.__class__.__name__, "\n")
    error_message = str(error).strip()
    if error_message != "":
        _s(red, error_message, "\n")
    _s()


def _display_complete(root, call_log, call_errors):
    n_errors = len(call_errors)

    if n_errors > 0:
        _s("\n")
        for error, error_formatted, stack in call_errors:
            _display_error(root, error, error_formatted, stack)

    _s(f"\nRan {len(call_log)} tests. ")
    if n_errors == 0:
        _s(green, "SUCCESS\n")
    else:
        _s(red, bold, f"{n_errors} ERROR(s)\n")


# Entrypoint
# ---------------------------------------------------------------------------------


def run_zests(
    root=None,
    include_dirs=None,
    allow_to_run="__all__",
    match_string=None,
    exclude_string=None,
    verbose=1,
    disable_shuffle=False,
    add_markers=False,
    bypass_skip=None,
    **kwargs,
):
    """
    root:
        The directory under which should be searched for zests and outside of which
        will be considered "library references" (greayed out in error messages)
    include_dirs:
        The folders (relative to root) that should be included in recursive search
    allow_to_run:
        If not None: A colon-delimited list of full test names (dot-delimited) that will be allowed to run
        Special values:
            __all__: Consider all zests to run
            __failed__: Consider previous failed zests
    match_string:
        If not None: A substring that if found in a zest name will include it
        Note: If allow_to_run includes only a subset of zests then this match_string
        can only further restrict the set. A match_string of None does not further restrict
        the list at all.
    exclude_string:
        If not None: A substring that if found in a zest name will exclude it
    verbose:
        0: no output
        1: normal output (dots notation)
        2: full test output (with names)
        3: debugging traces
    disable_shuffle:
        True: runs zests in consistent order.
        False (default): shuffles zests to root out order dependencies
    capture:
        If True, capture all stdio
    add_markers:
        Used for debugging. Ignore.
    bypass_skip:
        Used for debugging. Ignore.
    """
    zest.reset()
    zest._disable_shuffle = disable_shuffle
    n_zest_missing_errors = 0
    last_depth = 0
    curr_depth = 0
    results = {}

    # zest runner must start in the root of the project
    # so that modules may be loaded appropriately.
    root = root or os.getcwd()
    assert root[0] == os.sep

    root_zests, allow_to_run, errors = zest_finder.find_zests(
        root,
        include_dirs,
        allow_to_run.split(":"),
        match_string,
        exclude_string,
        bypass_skip,
    )

    for error in errors:
        parent_name, path, lineno, error_message = error

        _s(
            red,
            "\nERROR: ",
            reset,
            "Zest function ",
            bold,
            red,
            parent_name,
            reset,
            f" (@ {path}:{lineno}) ",
            f"{error_message}\n",
            f"If you are using local functions that are not tests, prefix them with underscore.\n",
        )

    if len(errors) > 0:
        return 1

    # Event functions are callbacks from zest
    # ---------------------------------------------------------------------------------
    def event_test_start(zest_result):
        """Track the callback depth and forward to the display_start()"""
        nonlocal last_depth, curr_depth
        if verbose >= 2:
            curr_depth = len(zest_result.call_stack) - 1
            _display_start(zest_result.short_name, last_depth, curr_depth, add_markers)
            last_depth = curr_depth

    def event_test_stop(zest_result):
        """
        Track the callback depth and forward to display_stop() or display_abbreviated()
        """
        nonlocal last_depth, curr_depth
        results[zest_result.full_name] = zest_result
        curr_depth = len(zest_result.call_stack) - 1
        if verbose >= 2:
            _display_stop(
                zest_result.error,
                zest_result.elapsed,
                zest_result.skip,
                last_depth,
                curr_depth,
            )
        elif verbose == 1:
            _display_abbreviated(zest_result.error, zest_result.skip)

    def event_complete():
        if verbose > 0:
            _display_complete(root, zest._call_log, zest._call_errors)

        if verbose > 1:
            _s("Slowest 5%\n")
            n_timings = len(results)
            timings = [
                (full_name, result.elapsed) for full_name, result in results.items()
            ]
            timings.sort(key=lambda tup: tup[1])
            ninety_percentile = 95 * n_timings // 100
            for i in range(n_timings - 1, ninety_percentile, -1):
                name = timings[i]
                _s("  ", name[0], gray, f" {int(1000.0 * name[1])} ms)\n")

        if verbose > 0:
            _display_warnings(zest._call_warnings)

    # LAUNCH root zests
    for (root_name, (module_name, package, full_path)) in root_zests.items():
        root_zest_func = zest_finder.load_module(root_name, module_name, full_path)
        zest.do(
            root_zest_func,
            test_start_callback=event_test_start,
            test_stop_callback=event_test_stop,
            allow_to_run=allow_to_run,
        )

    # Event functions are callbacks from zest
    # ---------------------------------------------------------------------------------

    event_complete()
    retcode = 0 if len(zest._call_errors) == 0 and n_zest_missing_errors == 0 else 1

    return retcode
