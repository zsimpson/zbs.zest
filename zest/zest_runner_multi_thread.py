"""
Multi-threaded runner runs each root zest in its own subprocess via
the zest_shim interface. Each root zest has its stdio redirected into
.out and .err files inside of which are also status message inserted
by the shim.

While running the poll() routine can be used to pluck out the status
messages live.

TODO:
This still has a major problem.
The load times are killing the performance.
Each root test runs python from the CLI.
That causes python to go through the whole complex import tree.
To avoid this I need python's loading context to be in the same process
and to have each test run as a fork from that process AFTER
the module is loaded so that all zests share the same import contexts.

So the fork has to happen just before the zest.do and after the load_module
And i have to redirect the stdio handles of the fork to the correct files.
So I think that means shim gets pulled into this file
and I can assign to sys.stdout = open it for write inside the child.

"""
import time
import json
import os
import re
import io
import random
import sys
import signal
from collections import deque
from pathlib import Path
from zest import zest
from zest import zest_finder
from zest.zest import log
from subprocess import Popen, DEVNULL
from dataclasses import dataclass


class ZestRunnerErrors(Exception):
    def __init__(self, errors):
        self.errors = errors


@dataclass
class RunnerProcess:
    root_name: str
    read_out: io.TextIOBase
    read_err: io.TextIOBase
    child_pid: int
    proc_i: int


pat = re.compile(r"\@\@\@(.+)\@\@\@")


def read_lines(fd, include_stdio):
    for line in fd:
        log(f"line={line}")
        m = re.match(pat, line)
        if m:
            try:
                yield json.loads(m.group(1))
            except json.JSONDecodeError:
                yield dict(error="decode error")
        elif include_stdio:
            yield line


class ZestRunnerMultiThread:
    def _start_next(self):
        try:
            next_zest = self.queue.popleft()
        except IndexError:
            return None

        curr_proc_iz = {proc.proc_i for proc in self.procs.values()}
        next_proc_i = random.choice(
            list(set(list(range(self.n_workers))) - curr_proc_iz)
        )

        root_name, module_name, package, full_path = next_zest

        out_path = os.path.join(self.output_folder, f"{root_name}.out")
        err_path = os.path.join(self.output_folder, f"{root_name}.err")

        log(f"start {root_name} {module_name} {full_path}")

        writ_out = open(out_path, "w", buffering=1)
        writ_err = open(err_path, "w", buffering=1)

        root_zest_func = zest_finder.load_module(root_name, module_name, full_path)

        child_pid, child_fd = os.forkpty()
        # If I use os.fork instead of forkpty then the child_pid captures stdio
        # even when I close it in the child_pid block.
        # But I'm not sure when to close the child_fd. I tried it in the parent
        # and the child. Parent it generates an exception and child it seems to
        # do nothing (based on lsof -a -p $PID listings before and after.)

        if child_pid == 0:
            # In the child process
            w1 = os.fdopen(writ_out.fileno(), "w", buffering=1)
            w2 = os.fdopen(writ_err.fileno(), "w", buffering=1)
            sys.stdout = w1
            sys.stderr = w2

            try:
                def emit(dict_, full_name, stream):
                    try:
                        print("@@@" + json.dumps(dict_) + "@@@", file=stream, flush=True)
                    except TypeError:
                        dict_ = dict(full_name=full_name, error="Serialization error")
                        print("@@@" + json.dumps(dict_) + "@@@", file=stream, flush=True)

                def event_callback(zest_result):
                    dict_ = dict(
                        full_name=zest_result.full_name,
                        error=repr(zest_result.error) if zest_result.error is not None else None,
                        error_formatted=zest_result.error_formatted,
                        is_running=zest_result.is_running,
                    )
                    emit(dict_, zest_result.full_name, sys.stdout)
                    emit(dict_, zest_result.full_name, sys.stderr)

                zest.do(
                    root_zest_func,
                    test_start_callback=event_callback,
                    test_stop_callback=event_callback,
                    allow_to_run=None,
                )

            finally:
                w1.close()
                w2.close()

            sys.exit(0)

        else:
            writ_out.close()
            writ_err.close()

            self.procs[root_name] = RunnerProcess(
                root_name,
                open(out_path, "r"),
                open(err_path, "r"),
                child_pid,
                next_proc_i,
            )

            self.n_run += 1

            return root_name

    def poll(self, event_callback, request_stop):
        """
        Check the status of all running threads
        Returns:
            True if there's most to do
            False if everything is done

        Usage:
            def callback(event_payload):
                ...

            runner = ZestRunnerMultiThread(...)
            while runner.poll(callback, request_stop):
                time.sleep(0.1)
                if ...: request_stop = True
        """
        if request_stop:
            for proc in self.procs.values():
                log(f"KILL {proc.child_pid}")
                os.kill(proc.child_pid, signal.SIGKILL)

        done = []
        for i, (root_name, proc) in enumerate(self.procs.items()):
            _, exit_status = os.waitpid(proc.child_pid, os.WNOHANG)

            if exit_status & 0xFF == 0:
                # Processes can be marked as dead before the buffers are flushed
                # so if I kill off the readers too soon I will miss the
                # last writes to the files. So I have to add exitted processes
                # into a separate list that is garbage collected after giving
                # the processes some time to settle.
                # It would be better if there was no buffering! But try as I might
                # I can not seem to get buffering turned off well enough to
                # stop this race.
                log(f"DONE {root_name} exit_status={exit_status}")
                done += [root_name]

            for payload in read_lines(proc.read_out, include_stdio=False):
                payload["proc_i"] = proc.proc_i
                payload["state"] = "started" if payload["is_running"] else "stopped"
                event_callback(payload)

        # HERE: I need to do some kind of clean up
        # for cleanup in self.cleanup:
        #     if

        for root_name in done:
            p = self.procs[root_name]
            p.read_out.close()
            p.read_err.close()
            del self.procs[root_name]

        while len(self.procs) < self.n_workers:
            full_name = self._start_next()
            if full_name is None:
                # All done
                break

            payload = dict(
                state="starting",
                full_name=full_name,
                proc_i=self.procs[full_name].proc_i,
            )
            event_callback(payload)

        if len(self.queue) == 0 and len(self.procs) == 0:
            return False

        return True

    def __init__(
        self,
        output_folder,
        root=None,
        include_dirs=None,
        allow_to_run="__all__",
        match_string=None,
        exclude_string=None,
        bypass_skip=None,
        n_workers=1,
        **kwargs,
    ):
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
        self.procs = {}
        self.queue = deque()
        self.n_run = 0
        self.clean_up = []

        for (root_name, (module_name, package, full_path)) in root_zests.items():
            self.queue.append((root_name, module_name, package, full_path))

        for _ in range(self.n_workers):
            self._start_next()
