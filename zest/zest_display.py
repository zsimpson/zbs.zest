import sys
import os
import re

blue = "\u001b[34m"
yellow = "\u001b[33m"
red = "\u001b[31m"
green = "\u001b[32m"
gray = "\u001b[30;1m"
cyan = "\u001b[36m"
magenta = "\u001b[35m"
bold = "\u001b[1m"
reset = "\u001b[0m"


def s(*strs):
    return sys.stdout.write("".join(strs) + reset)

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


def error_header(edge, edge_style, label):
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


def display_errors(errors):
    for error in errors:
        parent_name, path, lineno, error_message = error

        s(
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

def _display_error(root, error, error_formatted, stack):
    leaf_test_name = stack[-1]
    formatted_test_name = " . ".join(stack[0:-1]) + bold + " . " + leaf_test_name

    s("\n", error_header("=", red, formatted_test_name), "\n")
    lines = []
    for line in error_formatted:
        lines += [sub_line for sub_line in line.strip().split("\n")]

    is_libs = False
    for line in lines[1:-1]:
        split_line = traceback_match_filename(root, line)
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
    s()


def display_complete(root, call_log, call_errors):
    n_errors = len(call_errors)

    if n_errors > 0:
        s("\n")
        for error, error_formatted, stack in call_errors:
            _display_error(root, error, error_formatted, stack)

    s(f"\nRan {len(call_log)} tests. ")
    if n_errors == 0:
        s(green, "SUCCESS\n")
    else:
        s(red, bold, f"{n_errors} ERROR(s)\n")
