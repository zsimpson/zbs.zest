import time
import json
import os
import re
import io
import random
import sys
import signal
import multiprocessing
import traceback
import pathlib
from multiprocessing import Queue
from queue import Empty
from collections import deque
from pathlib import Path
from zest import zest
from zest.zest import ZestResult
from zest.zest_runner_base import ZestRunnerBase
from zest import zest_finder
from zest.zest import log
from subprocess import Popen, DEVNULL
from dataclasses import dataclass
from contextlib import redirect_stdout, redirect_stderr
from zest.zest_display import s, display_complete, display_timings, display_warnings


def read_zest_result_line(fd):
    while True:
        line = fd.readline()
        if not line:
            break

        if not isinstance(line, str):
            line = line.decode()

        yield ZestResult.loads(line)


def _do_work_order(root_name, module_name, package, full_path, output_folder, capture_stdio):
    event_stream = open(f"{output_folder}/{root_name}.evt", "wb", buffering=0)

    # It may be very slow to have the load_module here in the child
    # processes as it means that each child will have to load_module
    # and get no benefit from caching of modules. It might be better
    # to move this in to the parent process
    root_zest_func = zest_finder.load_module(root_name, module_name, full_path)

    zest_result_to_return = None

    try:
        def emit_zest_result(zest_result, stream):
            assert isinstance(zest_result, ZestResult)
            try:
                msg = (zest_result.dumps() + "\n").encode()
                stream.write(msg)
                stream.flush()
            except TypeError:
                log(f"Serialization error on {zest_result}")


        def event_callback(zest_result):
            """
            This callback occurs anytime a sub-zest starts or stops.
            """
            emit_zest_result(zest_result, event_stream)
            _do_work_order.queue.put(zest_result)
            nonlocal zest_result_to_return
            zest_result_to_return = zest_result

        zest._capture_stdio = capture_stdio
        zest.do(
            root_zest_func,
            test_start_callback=event_callback,
            test_stop_callback=event_callback,
            allow_to_run=None,
        )
    except Exception as e:
        e._formatted = traceback.format_exception(
            etype=type(e), value=e, tb=e.__traceback__
        )
        _do_work_order.queue.put(e)

    finally:
        event_stream.close()

    return zest_result_to_return


def _do_worker_init(queue):
    _do_work_order.queue = queue


class ZestRunnerMultiThread(ZestRunnerBase):
    def n_live_procs(self):
        return len([proc for proc in self.procs if proc.exit_code is None])

    def poll(self, request_stop):
        """
        Check the status of all running threads
        Returns:
            True if there's more to do
            False if everything is done

        Usage:
            def callback(zest_result):
                ...

            runner = ZestRunnerMultiThread(callback=callback, ...)
            while runner.poll(request_stop):
                time.sleep(0.1)
                if ...: request_stop = True
        """

        if request_stop:
            self.pool.terminate()
            # for proc in self.procs:
            #     if proc.exit_code is not None:
            #         try:
            #             os.kill(proc.child_pid, signal.SIGKILL)
            #         except ProcessLookupError:
            #             log(f"KILL failed {proc.child_pid}")

        try:
            while True:
                zest_result = self.queue.get_nowait()
                if isinstance(zest_result, Exception):
                    raise zest_result
                assert isinstance(zest_result, ZestResult)
                worker_i = self.pid_to_worker_i.get(zest_result.pid)
                if worker_i is None:
                    self.pid_to_worker_i[zest_result.pid] = len(self.pid_to_worker_i)
                zest_result.worker_i = self.pid_to_worker_i[zest_result.pid]
                self.worker_status[zest_result.worker_i] = zest_result
                if self.callback is not None:
                    self.callback(zest_result)
        except Empty:
            pass

        if self.results.ready() and self.queue.empty():
            self.pool.join()
            return False

        return True

    def __init__(self, n_workers=2, **kwargs):
        super().__init__(**kwargs)

        self.n_workers = n_workers
        self.pid_to_worker_i = {}
        self.worker_status = [None] * self.n_workers
        self.pool = None
        self.queue = Queue()

    def run(self):
        if self.retcode != 0:
            # CHECK that zest_find did not fail
            return self.retcode

        work_orders = [
            (root_name, module_name, package, full_path, self.output_folder, self.capture_stdio)
            for (root_name, (module_name, package, full_path)) in self.root_zests.items()
        ]

        n_status_lines = 0

        def cursor_move_to_start():
            """Move up n_status_lines"""
            sys.stdout.write(f"\033[{n_status_lines}A")

        # multiprocessing.Queue can only be passed via the pool initializer, not as an arg.
        with multiprocessing.Pool(self.n_workers, _do_worker_init, [self.queue]) as self.pool:
            self.results = self.pool.starmap_async(_do_work_order, work_orders)
            self.pool.close()

            # call_log = []
            # call_errors = []

            zest_results_path = pathlib.Path(".zest_results")
            zest_results_path.mkdir(parents=True, exist_ok=True)

            request_stop = False
            self.retcode = 0
            state_messages = ["DONE", "RUNNING"]
            wrote_status = False
            while True:
                try:
                    n_workers = len(self.worker_status)

                    # if ...: request_stop = True
                    if not self.poll(request_stop):
                        if wrote_status:
                            for _ in range(n_workers):
                                sys.stdout.write("\033[K\n")  # Clear to EOL and new line
                        break

                    for i, worker in enumerate(self.worker_status):
                        wrote_status = True
                        if worker is not None:
                            sys.stdout.write(f"{i:2d}: {state_messages[worker.is_running]:<8s} {worker.full_name}")
                        else:
                            sys.stdout.write(f"{i:2d}: NOT STARTED")
                        sys.stdout.write("\033[K\n")  # Clear to EOL and new line

                    n_status_lines = max(n_status_lines, n_workers)

                    cursor_move_to_start()

                    time.sleep(0.05)
                except KeyboardInterrupt:
                    request_stop = True
                    self.retcode = 1

            cursor_move_to_start()
            display_complete("", self.results.get())
