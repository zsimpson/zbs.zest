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
from zest import zest_finder
from zest.zest import log
from subprocess import Popen, DEVNULL
from dataclasses import dataclass
from contextlib import redirect_stdout, redirect_stderr
from zest.zest_display import *


class ZestRunnerErrors(Exception):
    def __init__(self, errors):
        self.errors = errors


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


def _do_worker_init(queue):
    _do_work_order.queue = queue


class ZestRunnerMultiThread:
    def n_live_procs(self):
        return len([proc for proc in self.procs if proc.exit_code is None])

    def poll(self, request_stop):
        """
        Check the status of all running threads
        Returns:
            True if there's more to do
            False if everything is done

        Usage:
            def callback(event_payload):
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
                self.callback(zest_result)
        except Empty:
            pass

        if all([r.ready() for r in self.results]) and self.queue.empty():
            self.pool.join()
            return False

        return True

    def __init__(
        self,
        output_folder,
        callback,
        root=None,
        include_dirs=None,
        allow_to_run="__all__",
        match_string=None,
        exclude_string=None,
        bypass_skip=None,
        n_workers=1,
        capture_stdio=False,
        **kwargs,
    ):
        self.callback = callback

        zest._bypass_skip = (bypass_skip or "").split(":")
        root_zests, allow_to_run, errors = zest_finder.find_zests(
            root,
            include_dirs,
            allow_to_run.split(":"),
            match_string,
            exclude_string,
            bypass_skip,
        )

        if len(errors) > 0:
            raise ZestRunnerErrors(errors)

        self.retcode = 0
        self.output_folder = output_folder
        self.n_workers = n_workers
        self.n_run = 0
        self.pid_to_worker_i = {}
        self.worker_status = [None] * self.n_workers
        self.capture_stdio = capture_stdio

        work_orders = [
            (root_name, module_name, package, full_path, output_folder, self.capture_stdio)
            for (root_name, (module_name, package, full_path)) in root_zests.items()
        ]

        self.queue = Queue()
        self.results = []

        # multiprocessing.Queue can only be passed via the pool initializer, not as an arg.
        with multiprocessing.Pool(self.n_workers, _do_worker_init, [self.queue]) as self.pool:
            self.results += [self.pool.starmap_async(_do_work_order, work_orders)]
            self.pool.close()
            self.pool.join()

            call_log = []
            call_errors = []

            def callback(zest_result):
                nonlocal call_log, call_errors
                if not zest_result.is_running:
                    call_log += [zest_result.full_name]
                    if zest_result.error is not None:
                        # TODO: Convert to using a simple list of results
                        call_errors += [
                            (
                                zest_result.error,
                                zest_result.error_formatted,
                                zest_result.full_name.split("."),
                            )
                        ]

            zest_results_path = pathlib.Path(".zest_results")
            zest_results_path.mkdir(parents=True, exist_ok=True)
            try:
                runner = ZestRunnerMultiThread(zest_results_path, callback, **kwargs)
                request_stop = False
                self.retcode = 0
                state_messages = ["DONE", "RUNNING"]
                wrote_status = False
                while True:
                    try:
                        n_workers = len(runner.worker_status)

                        # if ...: request_stop = True
                        if not runner.poll(request_stop):
                            if wrote_status:
                                for _ in range(n_workers):
                                    sys.stdout.write("\033[K\n")  # Clear to EOL and new line
                            break

                        for i, worker in enumerate(runner.worker_status):
                            wrote_status = True
                            if worker is not None:
                                sys.stdout.write(f"{i:2d}: {state_messages[worker.is_running]:<8s} {worker.full_name}")
                            else:
                                sys.stdout.write(f"{i:2d}: NOT STARTED")
                            sys.stdout.write("\033[K\n")  # Clear to EOL and new line

                        # GO UP to starting place
                        sys.stdout.write(f"\033[{n_workers}A")

                        time.sleep(0.05)
                    except KeyboardInterrupt:
                        request_stop = True
                        self.retcode = 1

                display_complete("", call_log, call_errors)

            except ZestRunnerErrors as e:
                display_errors(e.errors)
                self.retcode = 1


