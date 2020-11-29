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
        log(f"WTF {e._formatted}")
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

            # while True:
            #     try:
            #         msg = self.queue.get_nowait()
            #         log(f"MULTI 8 - msg", msg)
            #     except Empty:
            #         pass
            #
            #     time.sleep(0.1)
            #
            #     if all([r.ready() for r in self.results]) and self.queue.empty():
            #         log(f"MULTI 7 - done")
            #         break

            self.pool.join()


'''
class ZestRunnerMultiThread:
    def _start_next(self):
        try:
            next_zest = self.queue.popleft()
        except IndexError:
            return None

        curr_proc_iz = {proc.proc_i for proc in self.procs}
        if len(curr_proc_iz):
            next_proc_i = 0
        else:
            next_proc_i = random.choice(
                list(set(list(range(self.n_workers))) - curr_proc_iz)
            )

        # load_module must be in the parent process so that any
        # modules / sub-modules that are loaded in the following
        # line will be cached between zest roots. If we were to
        # defer the load_module to the child pid then it would be
        # very slow as it re-imported common modules over and over again
        root_name, module_name, package, full_path = next_zest
        root_zest_func = zest_finder.load_module(root_name, module_name, full_path)

        # CREATE pipe for the child to send messages to the parent
        # and open those as standard file handles.
        # These handles will be inherited into the child processes.
        # and the child will close the read and the parent will close the write.
        # The zeros are buffer size.
        # Note that you can only have unbuffered (0) on binary files!
        # This is annoying because it means that the file handles that
        # are used to emit event messages now must go through encode/decode
        r, w = os.pipe()
        r, w = os.fdopen(r, "rb", 0), os.fdopen(w, "wb", 0)

        child_pid, child_fd = os.forkpty()
        # If I use os.fork instead of forkpty then the child_pid captures stdio
        # even when I close it in the child_pid block.
        # But I'm not sure when to close the child_fd. I tried it in the parent
        # and the child. Parent it generates an exception and child it seems to
        # do nothing (based on lsof -a -p $PID listings before and after.)

        if child_pid:
            # Parent
            w.close()

            proc = RunnerProcess(root_name, r, child_pid, next_proc_i,)
            self.procs += [proc]
            self.n_run += 1
            return proc

        else:
            # Child
            r.close()

            event_stream = open(f"{self.output_folder}/{root_name}.evt", "wb", buffering=0)
            try:
                def emit_zest_result(zest_result, streams):
                    assert isinstance(zest_result, ZestResult)
                    try:
                        msg = (zest_result.dumps() + "\n").encode()
                        for stream in streams:
                            stream.write(msg)
                            stream.flush()
                    except TypeError:
                        log(f"Serialization error on {zest_result}")

                def event_callback(zest_result):
                    """
                    This callback occurs anytime a sub-zest starts or stops.
                    """

                    if zest_result.error:
                        log(f"EVENT CALLBACK ERROR '{repr(zest_result.error)}'")

                    # TODO: Write out the stdout and stderr returned in zest_result
                    # dict_ = dict(
                    #     full_name=zest_result.full_name,
                    #     error=repr(zest_result.error)
                    #     if zest_result.error is not None
                    #     else None,
                    #     error_formatted=zest_result.error_formatted,
                    #     is_running=zest_result.is_running,
                    #     skip=zest_result.skip,
                    # )
                    emit_zest_result(zest_result, (w, event_stream))

                zest.do(
                    root_zest_func,
                    test_start_callback=event_callback,
                    test_stop_callback=event_callback,
                    allow_to_run=None,
                )
                """
                [
                    (
                        NotImplementedError(),
                        [
                            'Traceback (most recent call last):\n', '  File "/erisyon/internal/overloads/zest/zest.py", line 570, in _run\n    func()\n', '  File "/erisyon/internal/internal/common/zests/zest_jri.py", line 19, in it_constructs_from_url\n    raise NotImplementedError\n', 'NotImplementedError\n'
                        ],
                        ['zest_construct_jri', 'it_constructs_from_url']
                    )
                ]
                """

                # emit_zest_result(zest._call_log, (w, event_stream))
                # emit(zest._call_errors, (w, event_stream))
            finally:
                event_stream.close()

        w.close()
        sys.exit(0)

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

        def read(proc):
            for zest_result in read_lines(proc.read_pipe):
                assert isinstance(zest_result, ZestResult)
                zest_result.worker_i = proc.proc_i
                self.callback(zest_result)

        if request_stop:
            for proc in self.procs:
                if proc.exit_code is not None:
                    try:
                        os.kill(proc.child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        log(f"KILL failed {proc.child_pid}")

        keep_running_procs = []
        for proc in self.procs:
            _, exit_code = os.waitpid(proc.child_pid, os.WNOHANG)
            read(proc)
            if exit_code & 0xFF == 0:
                proc.exit_time = time.time()
                proc.exit_code = exit_code
                # # Reap to prevent zombies
                # try:
                #     os.waitpid(proc.child_pid, 0)
                # except ChildProcessError:
                #     pass
            else:
                keep_running_procs += [proc]

        self.procs = keep_running_procs

        while self.n_live_procs() < self.n_workers:
            proc = self._start_next()
            if proc is None:
                # All done
                break

            # payload = dict(
            #     state="starting root", full_name=proc.root_name, proc_i=proc.proc_i, skip=None,
            # )
            # self.callback(payload)

        if len(self.queue) == 0 and self.n_live_procs() == 0:
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

        self.output_folder = output_folder
        self.n_workers = n_workers
        self.queue = deque()
        self.n_run = 0
        self.procs = []

        for (root_name, (module_name, package, full_path)) in root_zests.items():
            self.queue.append((root_name, module_name, package, full_path))

        for _ in range(self.n_workers):
            self._start_next()
'''