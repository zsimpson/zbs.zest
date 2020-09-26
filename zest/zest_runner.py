'''
# Launch several CLIs that emit things
import re
import time
import sys
import json
from subprocess import Popen, DEVNULL


fin_outs = []
fin_errs = []
procs = []

for i in range(3):
    fout = open(f"out_{i}", "w")
    ferr = open(f"err_{i}", "w")
    fin_outs += [open(f"out_{i}", "r")]
    fin_errs += [open(f"err_{i}", "r")]
    procs += [
        Popen(
            args=["python", "-u", "test.py", str(i)],
            bufsize=0,
            executable="python",
            stdin=DEVNULL,  # If I don't pass this then pudb can try to get in
            stdout=fout,
            stderr=ferr,
            cwd=None,
            env=None,
        )
    ]

pat = re.compile(r"\@\@\@(.+)\@\@\@")
ansi_escape = re.compile(r"(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]")

while True:
    n_done = 0
    for i, (p, fin_out, fin_err) in enumerate(zip(procs, fin_outs, fin_errs)):
        ret_code = p.poll()

        lines = fin_out.readlines()
        for line in lines:
            m = re.match(pat, line)
            if m:
                try:
                    payload = json.loads(m.group(1))
                    is_running = payload.get("is_running")
                    full_name = payload.get("full_name", "n/a")
                    if is_running is True:
                        print(f"start: {full_name}")
                    elif is_running is False:
                        print(f"stop: {full_name}")
                except json.JSONDecodeError:
                    pass
            else:
                line = ansi_escape.sub("", line)
                sys.stdout.write(f"{i} out: {line}")

        if ret_code is not None:
            n_done += 1

    if n_done == len(procs):
        break

    time.sleep(0.1)


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


run_lock = threading.Lock()


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

    def run(self):
        log(f"Process run")
        super().run()


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

        queue.put(("root_stop", ZestResult(root_name, "root_stop", "root_stop")))
    except Exception as e:
        log(f"_do_one_root_zest exception {e}")
        formatted = traceback.format_exception(
            etype=type(e), value=e, tb=e.__traceback__
        )
        log(formatted)
    finally:
        log(f"exit _do_one_root_zest {root_name}")

def async_result_done(result):
    log(f"async_result_done result={result}")
'''
