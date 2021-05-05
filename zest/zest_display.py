from zest import colors
import sys
import os
import re
import io
import traceback
from zest.zest import log


def s(*strs):
    for str_ in strs:
        if str_ is not None:
            sys.stdout.write(str_)
    sys.stdout.write(colors.reset)
    sys.stdout.flush()


_tb_pat = re.compile(r"^.*File \"([^\"]+)\", line (\d+), in (.*)")


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


def error_header(edge, edge_style, label, width=None):
    term_width = tty_size()[1]
    if width is None:
        width = term_width
    width = min(width, term_width)
    return (
        edge_style
        + (edge * 5)
        + " "
        + label
        + " "
        + colors.reset
        + edge_style
        + (edge * (width - 10 - len(label)))
    )


_tty_size_cache = None


def tty_size():
    global _tty_size_cache
    if _tty_size_cache is None:
        process = os.popen("stty size", "r")
        lines = process.read()
        retcode = process.close()
        if retcode is None:
            rows, cols = lines.split()
        else:
            rows, cols = 50, 80
        _tty_size_cache = (int(rows), int(cols))
    return _tty_size_cache


def display_find_errors(errors):
    s(colors.reset, colors.red, "Zest Finder Errors:\n")
    for error in errors:
        parent_name, path, lineno, error_message = error

        s(
            colors.reset,
            colors.bold,
            colors.red,
            "  ",
            parent_name,
            colors.reset,
            colors.yellow,
            f" (@ {path}:{lineno}) ",
            colors.red,
            f"{error_message}\n",
        )

    s(
        colors.yellow,
        f"Reminder: If you are using local functions that are not tests, prefix them with underscore.\n",
    )


def display_error(root, zest_result):
    stack = zest_result.full_name.split(".")
    leaf_test_name = stack[-1]
    formatted_test_name = " . ".join(stack[0:-1]) + colors.bold + " . " + leaf_test_name

    s("\n\n", error_header("=", colors.cyan, formatted_test_name), "\n")

    if zest_result.error is not None:
        s("\n", error_header("-", colors.yellow, "stdout", 40), "\n")
        s(zest_result.stdout)
        s("\n", error_header("-", colors.yellow, "stderr", 40), "\n")
        s(zest_result.stderr)

    lines = []
    for line in zest_result.error_formatted or [""]:
        lines += [sub_line for sub_line in line.strip().split("\n")]

    is_libs = False
    for line in lines[1:-1]:
        split_line = traceback_match_filename(root, line)
        if split_line is None:
            s(colors.gray if is_libs else "", line, "\n")
        else:
            leading, basename, lineno, context, is_libs = split_line
            if is_libs:
                s(colors.gray, "File ", leading, "/", basename)
                s(colors.gray, ":", lineno)
                s(colors.gray, " in function ")
                s(colors.gray, context, "\n")
            else:
                s(
                    "File ",
                    colors.yellow,
                    leading,
                    "/",
                    colors.yellow,
                    colors.bold,
                    basename,
                )
                s(":", colors.yellow, lineno)
                s(" in function ")
                if leaf_test_name == context:
                    s(colors.red, colors.bold, context, "\n")
                else:
                    s(colors.magenta, colors.bold, context, "\n")

    s(
        colors.red,
        "raised: ",
        colors.red,
        colors.bold,
        zest_result.error.__class__.__name__,
        "\n",
    )
    error_message = str(zest_result.error).strip()
    if error_message != "":
        s(colors.red, error_message, "\n")
    s()


def display_start(name, last_depth, curr_depth, add_markers):
    if last_depth is not None and curr_depth is not None:
        if last_depth < curr_depth:
            s("\n")

    if curr_depth is None:
        curr_depth = 0
    marker = "+" if add_markers else ""
    s("  " * curr_depth, colors.yellow, marker + name, colors.reset, ": ")
    # Note, no \n on this line because it will be added on the display_stop call


def display_stop(error, elapsed, skip, last_depth, curr_depth):
    if elapsed is None:
        elapsed = 0.0
    if last_depth is not None and curr_depth is not None:
        if curr_depth < last_depth:
            s(f"{'  ' * curr_depth}")
    if isinstance(error, str) and error.startswith("skipped"):
        s(colors.bold, colors.yellow, error)
    elif skip is not None:
        s(colors.bold, colors.yellow, "SKIPPED (reason: ", skip, ")")
    elif error:
        s(
            colors.bold,
            colors.red,
            "ERROR",
            colors.gray,
            f" (in {int(1000.0 * elapsed)} ms)",
        )
    else:
        s(colors.green, "SUCCESS", colors.gray, f" (in {int(1000.0 * elapsed)} ms)")
    s("\n")


def display_abbreviated(error, skip):
    if error:
        s(colors.bold, colors.red, "F")
    elif skip:
        s(colors.yellow, "s")
    else:
        s(colors.green, ".")


def display_complete(root, zest_results):
    results_with_errors = [res for res in zest_results if res.error]

    n_errors = len(results_with_errors)

    if n_errors > 0:
        for res in results_with_errors:
            display_error(root, res)

    s(f"\nRan {len(zest_results)} tests. ")
    if n_errors == 0:
        s(colors.green, "SUCCESS\n")
    else:
        s(colors.red, colors.bold, f"{n_errors} ERROR(s)\n")


def display_timings(results):
    s("Slowest 5%\n")
    n_timings = len(results)
    timings = [(result.full_name, result.elapsed) for result in results]
    timings.sort(key=lambda tup: tup[1])
    ninety_percentile = 95 * n_timings // 100
    for i in range(n_timings - 1, ninety_percentile, -1):
        name = timings[i]
        s("  ", name[0], colors.gray, f" {int(1000.0 * name[1])} ms)\n")


def display_warnings(call_warnings):
    for warn in call_warnings:
        s(colors.yellow, warn, "\n")


def colorful_exception(
    error=None,
    formatted=None,
    write_to_stderr=True,
    show_raised=True,
    compact=False,
    gray_libs=True,
):
    accum = ""

    def s(*strs):
        nonlocal accum
        accum += "".join(strs) + colors.reset

    tb_pat = re.compile(r"^.*File \"([^\"]+)\", line (\d+), in (.*)")

    def _traceback_match_filename(line):
        is_libs = False
        m = tb_pat.match(line)
        if m:
            file = m.group(1)
            lineno = m.group(2)
            context = m.group(3)
            real_path = os.path.realpath(file)
            relative_path = os.path.relpath(real_path)

            root = os.environ.get("ERISYON_ROOT")
            if root is not None:
                is_libs = True
                if real_path.startswith(root):
                    relative_path = re.sub(r".*/" + root, "./", real_path)
                    is_libs = False

            # Treat these long but commonly occurring path differently
            if "/site-packages/" in relative_path:
                relative_path = re.sub(r".*/site-packages/", ".../", relative_path)
            if "/dist-packages/" in relative_path:
                relative_path = re.sub(r".*/dist-packages/", ".../", relative_path)

            leading, basename = os.path.split(relative_path)
            # if leading and len(leading) > 0:
            #     leading = f"{'./' if leading[0] != '.' else ''}{leading}"
            return leading, basename, lineno, context, is_libs
        return None

    if not compact:
        s("\n")

    if formatted is None:
        formatted = traceback.format_exception(
            etype=type(error), value=error, tb=error.__traceback__
        )
    lines = []
    for line in formatted:
        lines += [sub_line for sub_line in line.strip().split("\n")]

    is_libs = False
    for line in lines[1:-1]:
        split_line = _traceback_match_filename(line)
        if split_line is None:
            s(gray if is_libs else "", line, "\n")
        else:
            leading, basename, lineno, context, is_libs = split_line
            if not gray_libs:
                is_libs = False
            if is_libs:
                s(gray, "File ", leading, "/", basename)
                s(gray, ":", lineno)
                s(gray, " in function ")
                s(gray, context, "\n")
            else:
                s(
                    "File ",
                    colors.yellow,
                    leading,
                    "/",
                    colors.yellow,
                    colors.bold,
                    basename,
                )
                s(":", colors.yellow, colors.bold, lineno)
                s(" in function ")
                s(colors.magenta, colors.bold, context, "\n")

    if show_raised:
        s(
            colors.red,
            "raised: ",
            colors.red,
            colors.bold,
            error.__class__.__name__,
            "\n",
        )
        error_message = str(error).strip()
        if error_message != "":
            s(colors.red, error_message, "\n")

    if write_to_stderr:
        sys.stderr.write(accum)

    return accum
