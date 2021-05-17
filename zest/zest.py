"""
A function-oriented testing framework for Python 3.

See README.md
"""
import logging
import os
import sys
import time
import inspect
import types
import traceback
import io
import re
import json
import tempfile
import shutil
import dataclasses
import ctypes
import tempfile
from tempfile import NamedTemporaryFile
from functools import wraps
from contextlib import contextmanager
from random import shuffle


log_fp = None
log_last_time = None


def log(*args):
    global log_fp, log_last_time
    if log_fp is None:
        log_fp = open("zest_log.txt", "a")
    delta = 0
    if log_last_time is not None:
        delta = time.time() - log_last_time
    log_last_time = time.time()
    #log_fp.write(f"{delta:3.1f} " + "".join([str(i) + " " for i in args]) + "\n")
    log_fp.write("".join([str(i) + " " for i in args]) + "\n")
    log_fp.flush()


ansi_escape = re.compile(r"(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]")


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


def human_readable_type_and_value(arg):
    """
    Examine the type of arg and emit it in friendly ways.
    """
    type_str = str(type(arg).__name__)
    val_str = str(arg)
    return type_str, val_str


class LogTrapFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if hasattr(record, "spy_variable_name"):
            # This record contains spy variable fields which are
            # colorized differently. See def spy() to see how these fields
            # go into this record.
            type_str, val_str = human_readable_type_and_value(record.msg)
            return f"{record.name}] {record.spy_variable_name}:{type_str} = {val_str}\n"
        else:
            return f"{record.name}] {record.msg}\n"

log_trap_formatter = LogTrapFormatter()


@contextmanager
def stdio_and_log_capture(should_capture):
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
        yield sys.stdout, sys.stderr, None
    else:
        global redirect_depth
        global so_root_save_fd, so_curr_tmpfile
        global se_root_save_fd, se_curr_tmpfile

        so_save_fd = os.dup(so_orig_fd)
        se_save_fd = os.dup(se_orig_fd)
        if redirect_depth == 0:
            so_root_save_fd = so_save_fd
            se_root_save_fd = se_save_fd

        so_tmpfile = NamedTemporaryFile(mode="w+", delete=False)
        se_tmpfile = NamedTemporaryFile(mode="w+", delete=False)
        lg_tmpfile = NamedTemporaryFile(mode="w+", delete=False)

        so_prev_tmpfile = so_curr_tmpfile
        se_prev_tmpfile = se_curr_tmpfile

        so_curr_tmpfile = so_tmpfile
        se_curr_tmpfile = se_tmpfile

        root_logger = logging.getLogger("")

        orig_root_logger_handlers = root_logger.handlers
        trap_handler = logging.StreamHandler(lg_tmpfile)
        trap_handler.setLevel(0)
        trap_handler.setFormatter(log_trap_formatter)
        root_logger.handlers = [trap_handler]

        redirect_depth += 1
        try:
            _redirect_stdout(so_tmpfile.fileno())
            _redirect_stderr(se_tmpfile.fileno())
            yield (so_tmpfile, se_tmpfile, lg_tmpfile)
            _redirect_stderr(se_save_fd)
            _redirect_stdout(so_save_fd)
        finally:
            redirect_depth -= 1
            so_tmpfile.close()
            se_tmpfile.close()
            lg_tmpfile.close()
            so_curr_tmpfile = so_prev_tmpfile
            se_curr_tmpfile = se_prev_tmpfile
            os.close(so_save_fd)
            os.close(se_save_fd)
            root_logger.handlers = orig_root_logger_handlers


@contextmanager
def pause_stdio_capture():
    if redirect_depth > 0:
        _redirect_stdout(so_root_save_fd)
        _redirect_stderr(se_root_save_fd)
        yield
        _redirect_stdout(so_curr_tmpfile.fileno())
        _redirect_stderr(se_curr_tmpfile.fileno())
    else:
        yield


def strip_ansi(line):
    return ansi_escape.sub("", line)


def get_class_or_module_that_defined_method(meth):
    # From https://stackoverflow.com/questions/3589311/get-defining-class-of-unbound-method-object-in-python-3/25959545#25959545
    if inspect.ismethod(meth):
        for cls in inspect.getmro(meth.__self__.__class__):
            if cls.__dict__.get(meth.__name__) is meth:
                return cls
        meth = meth.__func__

    if inspect.isfunction(meth):
        cls = getattr(
            inspect.getmodule(meth),
            meth.__qualname__.split(".<locals>", 1)[0].rsplit(".", 1)[0],
        )
        if isinstance(cls, type):
            return cls
        else:
            return inspect.getmodule(meth)


class TrappedException(Exception):
    """
    This will be passed back from a with zest.raises(SomeException) as e.
    It has one parameter: exception.

    Example:
        with zest.raises(SomeException) as e:
            something()
        assert e.exception.property == 1
    """

    pass


class SkipException(Exception):
    def __init__(self, full_name, reason):
        self.full_name = full_name
        self.reason = reason


class MockFunction:
    def __init__(self, replacing_func=None):
        if replacing_func is not None:
            self.arg_spec = inspect.getfullargspec(replacing_func)
        else:
            self.arg_spec = None
        self.list_of_exceptions_to_raise = None
        self.exception_to_raise = None
        self.list_of_values_to_return = None
        self.value_to_return = None
        self.hook_to_call = None
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls += [(args, kwargs)]

        if self.hook_to_call is not None:
            return self.hook_to_call(*args, **kwargs)

        # EXCEPTION from series or repeatedly if requested
        if self.list_of_exceptions_to_raise is not None:
            if len(self.list_of_exceptions_to_raise) == 0:
                raise AssertionError(
                    "mock was called more times than the list_of_exceptions_to_raise had elements"
                )
            raise self.list_of_exceptions_to_raise.pop(0)

        if self.exception_to_raise is not None:
            raise self.exception_to_raise

        # RETURN from series or repeatedly
        if self.list_of_values_to_return is not None:
            if len(self.list_of_values_to_return) == 0:
                raise AssertionError(
                    "mock was called more times than the list_of_values_to_return had elements"
                )
            return self.list_of_values_to_return.pop(0)
        return self.value_to_return

    @property
    def n_calls(self):
        return len(self.calls)

    def reset(self):
        self.calls = []

    def hook(self, fn_to_call):
        self.hook_to_call = fn_to_call

    def returns(self, value_to_return):
        self.value_to_return = value_to_return

    def returns_serially(self, list_of_values_to_return):
        self.list_of_values_to_return = list_of_values_to_return

    def exceptions(self, exception_to_raise):
        self.exception_to_raise = exception_to_raise

    def exceptions_serially(self, list_of_exceptions_to_raise):
        self.list_of_exceptions_to_raise = list_of_exceptions_to_raise

    def called_once_with(self, *args, **kwargs):
        return (
            len(self.calls) == 1
            and self.calls[0][0] == args
            and self.calls[0][1] == kwargs
        )

    def called(self):
        return len(self.calls) > 0

    def called_once(self):
        return len(self.calls) == 1

    def not_called(self):
        return len(self.calls) == 0

    def normalized_calls(self):
        """
        Converts the calls into a list of kwargs by combining the args and kwargs.
        This simplifies assert handling in many cases where you don't care if
        the arguments were passed by position of name.
        """
        arg_spec = [arg for arg in self.arg_spec.args if arg != "self"]

        # arg_spec is now a list of all positional argument names that the real function
        # expects (excluding special *, **)

        normalized_calls = []
        for by_pos, by_keyword in self.calls:
            # COVERT all the arguments that were passed in without keywords...
            normalized_args = {
                arg_spec[i]: passed_value for i, passed_value in enumerate(by_pos)
            }

            # ADD in those arguments that were passed by keyword.
            normalized_args.update(by_keyword)

            normalized_calls += [normalized_args]

        return normalized_calls

    def normalized_call(self):
        """Used when you expect only one call and are checking some argument"""
        assert self.n_calls == 1
        return self.normalized_calls()[0]

    def called_once_with_kws(self, **kws):
        """
        Returns True if the mocked was called only once and with its args and kwargs
        normalized into the kws specified as the arguments to this func.
        """
        if self.n_calls != 1:
            return False

        return kws == self.normalized_calls()[0]


class JSONDataClassEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, BaseException):
            return f"{o.__class__.__name__}(\"{str(o)}\")"
        # if isinstance(o, ZestResult):
        #     if o.error is not None:
        #         try:
        #             dataclasses.asdict(o)
        #         except Exception as e:
        #             # If it can not be encoded convert to str
        #             o.error = Exception(f"{o.error.__class__.__name__}: \"{str(o.error)}\"")
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return super().default(o)


@dataclasses.dataclass
class ZestResult:
    call_stack: list
    full_name: str
    short_name: str
    error: str = None
    error_formatted: str = None
    elapsed: float = None
    skip: str = None
    stdout: str = None
    stderr: str = None
    logs: str = None
    source: str = None
    pid: int = None
    is_running: bool = False
    is_starting: bool = False
    worker_i: int = 0

    def dumps(self):
        return json.dumps(self, cls=JSONDataClassEncoder)

    @classmethod
    def loads(cls, s):
        return ZestResult(**json.loads(s))


def check_allow_to_run(allow_list, test_name_parts):
    full_name = ".".join(test_name_parts)
    if allow_list is None:
        allow = True
    else:
        allow = False
        for allow_name in allow_list:
            if allow_name == "__all__":
                allow = True
                break
            elif allow_name.endswith("."):
                allow_parts = allow_name.split(".")[0:-1]
                n_allow_parts = len(allow_parts)
                allow = all([
                    test_name_parts[i] == allow_parts[i]
                    for i in range(n_allow_parts)
                ])
                if allow:
                    break
            elif full_name == allow_name:
                allow = True
                break
    return allow


class zest:
    """
    This is a helper to make calling a little bit cleaner.

    Example:

    from plaster.tools.zest.zest import zest

    def some_test():
        def test_1():
            with zest.raises(TypeError):
                example.call_some_global_func()

        def test_2():
            with zest.mock(example.some_global_func):
                example.some_global_func()

        zest()

    zest(some_test)
    """

    # TODO: Convert these to just use a list of zest_results
    _call_log = []
    _call_stack = []
    _call_errors = []
    _call_warnings = []
    _call_tree = []
    _test_start_callback = None
    _test_stop_callback = None
    _mock_stack = []
    _allow_to_run = None
    _disable_shuffle = False
    _capture = False
    _bypass_skip = []
    _current_error = None   # Useful for checks by _after
    _common_tmp = None
    _tmp_root = None

    @staticmethod
    def reset(disable_shuffle=False, bypass_skip=None, common_tmp=None, tmp_root=None, capture=None):
        zest._call_log = []
        zest._call_stack = []
        zest._call_errors = []
        zest._call_warnings = []
        zest._call_tree = []
        zest._test_start_callback = None
        zest._test_stop_callback = None
        zest._mock_stack = []
        zest._allow_to_run = None
        zest._capture = capture
        zest._disable_shuffle = disable_shuffle
        zest._bypass_skip = [] if bypass_skip is None else bypass_skip.split(":")
        zest._common_tmp = common_tmp
        zest._tmp_root = tmp_root

    @staticmethod
    def current_test_name():
        return zest._call_stack[-1]

    @staticmethod
    def current_test_full_name():
        return ".".join(zest._call_stack)

    @staticmethod
    def current_test_error():
        """
        Current error is a useful state to check in _after()
        """
        return zest._current_error

    # TODO: Sort out all the naming conventions for this
    # @staticmethod
    # def parameter_list(params_list):
    #     """
    #     Params list is a list of tuples that will be passed to the *args.
    #     If param_list is not a list of tuples they will be converted to tuples
    #     """
    #
    #     def decorator(fn):
    #         @wraps(fn)
    #         def wrapper(*params, **kwargs):
    #             fn(*params, **kwargs)
    #
    #         _params_list = [
    #             params if isinstance(params, tuple) else (params,)
    #             for params in params_list
    #         ]
    #         setattr(wrapper, "params_list", _params_list)
    #
    #         return wrapper
    #
    #     return decorator

    @staticmethod
    def skip(reason=None):
        def decorator(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                full_name = ".".join(zest._call_stack)
                if full_name not in zest._bypass_skip:
                    raise SkipException(full_name, reason)
                else:
                    fn(*args, **kwargs)

            setattr(wrapper, "skip", True)
            setattr(wrapper, "skip_reason", reason)
            return wrapper

        return decorator

    @staticmethod
    def group(name):
        def decorator(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                return fn(*args, **kwargs)

            setattr(wrapper, "group", name)
            return wrapper

        return decorator

    @staticmethod
    def retry(n_tries):
        def decorator(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                tries = n_tries
                while tries > 0:
                    try:
                        return fn(*args, **kwargs)
                    except Exception as e:
                        tries -= 1
                        if tries == 0:
                            raise e
            return wrapper

        return decorator

    @staticmethod
    def _setup_mock(symbol, substitute_fn=None):
        if not callable(symbol):
            raise AssertionError(f"Unmockable symbol {symbol} (must be callable)")

        old = None
        klass = None
        if substitute_fn is not None:
            new = substitute_fn
        else:
            new = MockFunction(symbol)

        klass = get_class_or_module_that_defined_method(symbol)
        # if isinstance(klass, types.ModuleType):
        #     frame = inspect.currentframe()
        #     module = inspect.getmodule(frame.f_back.f_back)
        #     for name, obj in inspect.getmembers(module):
        #         if (
        #             hasattr(obj, "__qualname__")
        #             and obj.__qualname__ == symbol.__qualname__
        #         ):
        #             raise AssertionError(
        #                 f"You are mocking the module-level symbol {symbol.__qualname__} which "
        #                 f"is imported directly into the test module. You should instead "
        #                 f"import the containing module and then mock the sub-symbol."
        #             )

        old = getattr(klass, symbol.__name__)
        setattr(klass, symbol.__name__, new)
        return old, klass, new

    @staticmethod
    def _clear_stack_mocks():
        for (klass, symbol, old, new, reset_before_each) in zest._mock_stack[-1]:
            setattr(klass, symbol.__name__, old)

    @staticmethod
    def stack_mock(
        symbol,
        reset_before_each=True,
        returns=None,
        returns_serially=None,
        substitute_fn=None,
    ):
        old, klass, new = zest._setup_mock(symbol, substitute_fn=substitute_fn)
        if returns is not None:
            new.returns(returns)
        elif returns_serially is not None:
            new.returns_serially(returns_serially)
        zest._mock_stack[-1] += [(klass, symbol, old, new, reset_before_each)]
        return new

    @staticmethod
    @contextmanager
    def mock(symbol, returns=None, hook=None):
        old, klass, new = None, None, None
        try:
            old, klass, new = zest._setup_mock(symbol)
            if returns is not None:
                new.returns(returns)
            if hook is not None:
                new.hook(hook)
            yield new
        finally:
            if klass and old:
                setattr(klass, symbol.__name__, old)

    @staticmethod
    @contextmanager
    def raises(expected_exception=Exception, **kwargs):
        """
        Use this in the inner most test. Do not attempt to encapsulate
        more than one test with this context. See README.

        The kwargs can include statements about the exception

            in_args=value
            in_{key}=substring
            key=val
        """
        got_expected_exception = False
        trapped_exception = TrappedException()
        try:
            yield trapped_exception
        except expected_exception as actual_e:
            trapped_exception.exception = actual_e
            got_expected_exception = True

        if got_expected_exception:
            # Check keys in the exception
            for key, val in kwargs.items():
                if key.startswith("in_args"):
                    for arg in trapped_exception.exception.args:
                        if val in arg:
                            break
                    else:
                        raise AssertionError(
                            f"expected exception to have '{val}' in some arg but not found in "
                            f"{trapped_exception.exception.args}"
                        )
                elif key.startswith("in_"):
                    key = key[3:]
                    if val not in getattr(trapped_exception.exception, key):
                        raise AssertionError(
                            f"expected exception to have '{val}' in key '{key}'. "
                            f"Found '{getattr(trapped_exception.exception, key)}'"
                        )
                else:
                    if val != getattr(trapped_exception.exception, key):
                        raise AssertionError(
                            f"expected exception to have '{key}' == '{val}'. "
                            f"Found '{getattr(trapped_exception.exception, key)}'"
                        )
        else:
            raise AssertionError(
                f"expected {expected_exception} but nothing was raised."
            )

        # If some other exception was raised that should just bubble as usual

    @staticmethod
    def current_test_name():
        return zest._call_stack[-1]

    @staticmethod
    def do(
        *funcs, test_start_callback=None, test_stop_callback=None, allow_to_run=None
    ):
        """
        This is the entrypoint of any zest at any depth.

        It is called by zest_runner in the case of "root" level
        tests. But each of those tests can call this recursively.

        Eg:

            def zest_test1():  # <-- This is the root level recursion called from zest_runner
                def it_does_x():
                    a = b()
                    assert a == 1

                def it_does_y():
                    a = c()
                    assert a == 2

                    def it_does_y1():
                        assert something()

                    zest()  # <-- This is the second sub-root level recursion

                zest()  # <-- This is the first sub-root level recursion

        This function works by looking up the call stack and analyzing
        the caller's scope to find functions that do not start with underscore
        and for two special underscore function: _before and _after...

        Call _before() (if defined) before each test
        Call _after() (if defined) after each test

        The class member _allow_to_run potentially contains a list of
        zests that are allowed to execute in dotted form. Eg using above:
            ["zest_test1.it_does_y.it_does_y1"]

        This example would mean that "zest_test1" would run and "zest_test1.it_does_y"
        and "zest_test1.it_does_y.it_does_y1"

        When a parent level is given, all its children will run too.
        Eg: ["zest_test1.it_does_y"] means that it_does_y1 will run too.

        """
        prev_test_start_callback = None
        prev_test_stop_callback = None
        prev_allow_to_run = None
        if test_start_callback is not None:
            prev_test_start_callback = zest._test_start_callback
            zest._test_start_callback = test_start_callback
        if test_stop_callback is not None:
            prev_test_stop_callback = zest._test_stop_callback
            zest._test_stop_callback = test_stop_callback
        if allow_to_run is not None:
            prev_allow_to_run = zest._allow_to_run
            zest._allow_to_run = allow_to_run

        try:
            callers_special_local_funcs = {}

            if len(funcs) > 0:
                funcs_to_call = [
                    (func.__name__, func)
                    for func in funcs
                    if isinstance(func, types.FunctionType)
                    and not func.__name__.startswith("_")
                ]
            else:
                # Extract test functions from caller's scope
                frame = inspect.currentframe()
                try:
                    zest_module_name = inspect.getmodule(frame).__name__
                    while inspect.getmodule(frame).__name__ == zest_module_name:
                        frame = frame.f_back

                    context = frame.f_locals

                    callers_special_local_funcs = {
                        name: func
                        for name, func in context.items()
                        if isinstance(func, types.FunctionType)
                        and name.startswith("_")
                        and not isinstance(func, MockFunction)
                    }

                    funcs_to_call = [
                        (name, func)
                        for name, func in context.items()
                        if isinstance(func, types.FunctionType)
                        and not name.startswith("_")
                        and not isinstance(func, MockFunction)
                    ]
                finally:
                    del frame

            # Randomly shuffle test order to reveal accidental order dependencies.
            # TASK: make this a flag that is called during staging (w/ multi-run)
            funcs_to_call = sorted(funcs_to_call, key=lambda x: x[0])
            if len(funcs_to_call) > 1:
                if not zest._disable_shuffle:
                    shuffle(funcs_to_call)

            _begin = callers_special_local_funcs.get("_begin")
            if _begin is not None:
                raise ValueError(
                    "A _begin function was declared. Maybe you meant _before?"
                )

            for name, func in funcs_to_call:
                if len(zest._mock_stack) > 0:
                    for mock_tuple in zest._mock_stack[-1]:
                        if mock_tuple[4]:  # if reset_before_each is set
                            mock_tuple[3].reset()  # Tell the mock to reset

                with stdio_and_log_capture(zest._capture) as (so, se, lg):
                    zest._call_stack += [name]
                    zest._current_error = None

                    try:
                        full_name = ".".join(zest._call_stack)
                        allow = check_allow_to_run(zest._allow_to_run, zest._call_stack)
                        if not allow:
                            # if (
                            #     zest._allow_to_run is not None
                            #     and full_name not in zest._allow_to_run
                            #     and zest._allow_to_run != "__all__"
                            # ):
                            zest._call_stack.pop()
                            continue

                    except Exception as e:
                        log(f"EXCEPTION during allow to check run. NAME {name} e {e}")
                        zest._call_stack.pop()
                        continue

                    pre_cwd = os.getcwd()
                    remove_tmp_dir = None
                    try:
                        if zest._common_tmp is not None:
                            cwd = zest._common_tmp
                        else:
                            # Create a tmp folder per test
                            tmp_root = zest._tmp_root or "/tmp"
                            cwd = tempfile.mkdtemp(dir=tmp_root)
                            remove_tmp_dir = cwd

                        # Set each test into the correct tmp folder
                        os.chdir(cwd)

                        # for params in params_list:
                        _before = callers_special_local_funcs.get("_before")
                        if _before:
                            try:
                                _before()
                            except Exception as e:
                                zest._call_errors += [(e, zest._call_stack.copy())]
                                s = (
                                    f"There was an exception while running '_before()' in test '{name}'. "
                                    f"This may mean that the sub-tests are not enumerated and therefore can not be run."
                                )
                                zest._call_warnings += [s]

                        try:
                            zest._call_tree += [full_name]
                            zest._call_log += [full_name]

                            if zest._test_start_callback:
                                with pause_stdio_capture():

                                    zest._test_start_callback(
                                        ZestResult(
                                            zest._call_stack,
                                            full_name,
                                            zest._call_stack[-1],
                                            None,
                                            None,
                                            None,
                                            None,
                                            None,
                                            None,
                                            func.__code__.co_filename,
                                            os.getpid(),
                                            True,
                                        )
                                    )

                            error = None
                            error_formatted = None
                            skip_reason = None
                            start_time = time.time()
                            try:
                                zest._mock_stack += [[]]

                                try:
                                    func()
                                except SkipException as e:
                                    skip_reason = e.reason
                                zest._clear_stack_mocks()
                                zest._mock_stack.pop()
                            except Exception as e:
                                error = e
                                error_formatted = traceback.format_exception(
                                    etype=type(error), value=error, tb=error.__traceback__
                                )
                                zest._call_errors += [1]
                                # zest._call_errors += [
                                #     (e, error_formatted, zest._call_stack.copy())
                                # ]
                                zest._current_error = e
                            finally:
                                stop_time = time.time()

                                try:
                                    sys.stdout.flush()
                                    sys.stderr.flush()
                                    so.flush()
                                    se.flush()
                                    if lg is not None:
                                        lg.flush()
                                except OSError:
                                    pass

                                captured_so = None
                                try:
                                    so.seek(0, io.SEEK_SET)
                                    captured_so = so.read()
                                except io.UnsupportedOperation:
                                    # This happens if so is actually sys.stdout
                                    pass
                                except OSError:
                                    pass

                                captured_se = None
                                try:
                                    se.seek(0, io.SEEK_SET)
                                    captured_se = se.read()
                                except io.UnsupportedOperation:
                                    # This happens if se is actually sys.stderr
                                    pass

                                captured_lg = None
                                if lg is not None:
                                    try:
                                        lg.seek(0, io.SEEK_SET)
                                        captured_lg = lg.read()
                                    except io.UnsupportedOperation:
                                        # This happens if so is actually sys.stdout
                                        pass
                                    except OSError:
                                        pass

                                if zest._test_stop_callback:
                                    if error is not None:
                                        error = f"{error.__class__.__name__}: \"{str(error)}\""

                                    zest_result = ZestResult(
                                        zest._call_stack,
                                        ".".join(zest._call_stack),
                                        zest._call_stack[-1],
                                        error,
                                        error_formatted,
                                        stop_time - start_time,
                                        skip_reason,
                                        captured_so if captured_so is not None else None,
                                        captured_se if captured_se is not None else None,
                                        captured_lg if captured_lg is not None else None,
                                        func.__code__.co_filename,
                                        os.getpid(),
                                        False,
                                    )
                                    with pause_stdio_capture():
                                        zest._test_stop_callback(zest_result)

                            _after = callers_special_local_funcs.get("_after")
                            if _after:
                                _after()
                        except Exception as e:
                            log(f"ZEST EXCEPTION 1 {e}")
                        finally:
                            zest._call_stack.pop()
                    finally:
                        # Clean up tmp folders if needed
                        if remove_tmp_dir:
                            try:
                                shutil.rmtree(remove_tmp_dir)
                            except OSError as exc:
                                if exc.errno != errno.ENOENT:  # ENOENT - no such file or directory
                                    raise  # re-raise exception
                        os.chdir(pre_cwd)

        finally:
            if prev_test_start_callback is not None:
                zest._test_start_callback = prev_test_start_callback
            if prev_test_stop_callback is not None:
                zest._test_stop_callback = prev_test_stop_callback
            if prev_allow_to_run is not None:
                zest._allow_to_run = prev_allow_to_run

    def __init__(self, *args, **kwargs):
        self.do(*args, **kwargs)
