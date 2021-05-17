import sys
import os
import io
import ctypes
from tempfile import NamedTemporaryFile
import time
from dataclasses import dataclass
from contextlib import contextmanager

log_fp = None
log_last_time = None
def log(*args):
    global log_fp, log_last_time
    if log_fp is None:
        log_fp = open("log.txt", "a")
    delta = 0
    if log_last_time is not None:
        delta = time.time() - log_last_time
    log_last_time = time.time()
    log_fp.write(f"{delta:3.1f} " + "".join([str(i) + " " for i in args]) + "\n")
    log_fp.flush()


# Redirection is re-entrant and pauseable
libc = ctypes.CDLL(None)
redirect_depth = 0

so_orig_fd = sys.stdout.fileno()  # The root level handle for stdout (typically == 1)
so_root_save_fd = None  # Will be set for the root level stdout so that it can be used in pause
so_c_fd = None  # The libc handle so that it can be flushed
so_curr_tmpfile = None  # The top of the so stack tmpfile which is needed by pause/resume

se_orig_fd = sys.stderr.fileno()  # The root level handle for stderr (typically == 2)
se_root_save_fd = None  # Will be set for the root level stderr so that it can be used in pause
se_c_fd = None  # The libc handle se that it can be flushed
se_curr_tmpfile = None  # The top of the se stack tmpfile which is needed by pause/resume

try:
    # Linux
    so_c_fd = ctypes.c_void_p.in_dll(libc, 'stdout')
except ValueError:
    # OSX
    so_c_fd = ctypes.c_void_p.in_dll(libc, '__stdoutp')

try:
    # Linux
    se_c_fd = ctypes.c_void_p.in_dll(libc, 'stderr')
except ValueError:
    # OSX
    se_c_fd = ctypes.c_void_p.in_dll(libc, '__stderrp')


def _redirect_stdout(to_fd):
    libc.fflush(so_c_fd)
    sys.stdout.close()
    os.dup2(to_fd, so_orig_fd)
    sys.stdout = io.TextIOWrapper(os.fdopen(so_orig_fd, "wb"))


def _redirect_stderr(to_fd):
    libc.fflush(se_c_fd)
    sys.stderr.close()
    os.dup2(to_fd, se_orig_fd)
    sys.stderr = io.TextIOWrapper(os.fdopen(se_orig_fd, "wb"))


@contextmanager
def stdio_capture(should_capture):
    """
    Capture stdout in a re-entrant manner. See pause_stdio_capture().

    If should_capture is False it simply returns (stdout, stderr)
    which simplifies conditional "with" clauses. Ie:

        with stdio_capture(should_capture) as (so, se):
            important_stuff(so, se)

    as opposed to:

        if should_capture:
            with stdio_capture(should_capture) as (so, se):
                important_stuff(so, se)
        else:
            # repeating the above
            important_stuff(sys.stdout, sys.stderr)

    """

    if not should_capture:
        yield sys.stdout, sys.stderr
    else:
        global redirect_depth
        global so_root_save_fd, so_curr_tmpfile
        global se_root_save_fd, se_curr_tmpfile

        so_save_fd = os.dup(so_orig_fd)
        se_save_fd = os.dup(se_orig_fd)
        if redirect_depth == 0:
            so_root_save_fd = so_save_fd
            se_root_save_fd = se_save_fd

        so_tmpfile = NamedTemporaryFile(mode="w+b")
        se_tmpfile = NamedTemporaryFile(mode="w+b")

        so_prev_tmpfile = so_curr_tmpfile
        se_prev_tmpfile = se_curr_tmpfile

        so_curr_tmpfile = so_tmpfile
        se_curr_tmpfile = se_tmpfile

        redirect_depth += 1
        try:
            _redirect_stdout(so_tmpfile.fileno())
            _redirect_stderr(se_tmpfile.fileno())
            yield (so_tmpfile, se_tmpfile)
            _redirect_stderr(se_save_fd)
            _redirect_stdout(so_save_fd)
        finally:
            redirect_depth -= 1
            so_tmpfile.close()
            se_tmpfile.close()
            so_curr_tmpfile = so_prev_tmpfile
            se_curr_tmpfile = se_prev_tmpfile
            os.close(so_save_fd)
            os.close(se_save_fd)


@contextmanager
def pause_stdio_capture():
    if redirect_depth > 0:
        log(f"  pause redirect")
        _redirect_stdout(so_root_save_fd)
        _redirect_stderr(se_root_save_fd)
        yield
        log(f"  resume redirect")
        _redirect_stdout(so_curr_tmpfile.fileno())
        _redirect_stderr(se_curr_tmpfile.fileno())
    else:
        yield


# The tricky thing is that I have to support recursion
depth = 0

def start_test():
    global depth

    with stdio_capture(True) as (so, se):

        with pause_stdio_capture():
            print(f"Running {depth}")

        print(f"This is the test stdout at {depth=}", file=sys.stdout)
        print(f"This is the test stderr at {depth=}", file=sys.stderr)

        if depth < 1:
            depth += 1
            start_test()
            depth -= 1

        sys.stdout.flush()
        sys.stderr.flush()
        so.flush()
        se.flush()
        so.seek(0, io.SEEK_SET)
        se.seek(0, io.SEEK_SET)
        captured_so = ""
        try:
            captured_so = so.read()
        except io.UnsupportedOperation:
            # This happens if so is actually sys.stdout
            pass

        captured_se = ""
        try:
            captured_se = se.read()
        except io.UnsupportedOperation:
            # This happens if se is actually sys.stderr
            pass

        with pause_stdio_capture():
            print(f"Back {depth}")
            print(f"Capture stdout was '{captured_so}'")
            print(f"Capture stderr was '{captured_se}'")

if __name__ == "__main__":
    start_test()
