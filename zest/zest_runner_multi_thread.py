import json
import os
import re
import io
from collections import deque
from pathlib import Path
from zest import zest_finder
from subprocess import Popen, DEVNULL
from dataclasses import dataclass


class ZestRunnerErrors(Exception):
    def __init__(self, errors):
        self.errors = errors


@dataclass
class RunnerProcess:
    writ_out: io.TextIOBase
    writ_err: io.TextIOBase
    read_out: io.TextIOBase
    read_err: io.TextIOBase
    proc: Popen


class ZestRunnerMultiThread:
    pat = re.compile(r"\@\@\@(.+)\@\@\@")
    ansi_escape = re.compile(r"(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]")

    def _start_next(self):
        try:
            next_zest = self.queue.popleft()
        except IndexError:
            return False

        root_name, module_name, package, full_path = next_zest

        out_path = os.path.join(self.output_folder, f"{root_name}.out")
        err_path = os.path.join(self.output_folder, f"{root_name}.err")

        writ_out = open(out_path, "w")
        writ_err = open(err_path, "w")
        proc = Popen(
            args=["python", "-u", "-m", "zest.zest_shim", root_name, module_name, full_path],
            bufsize=0,
            executable="python",
            stdin=DEVNULL,  # DEVNULL here prevents pudb from taking over
            stdout=writ_out,
            stderr=writ_err,
        )
        self.procs[root_name] = RunnerProcess(
            writ_out, writ_err, open(out_path, "r"), open(err_path, "r"), proc
        )

        return True

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
            raise NotImplementedError

        done = []
        for i, (root_name, proc) in enumerate(self.procs.items()):
            ret_code = proc.proc.poll()

            for line in proc.read_out:
                m = re.match(self.pat, line)
                if m:
                    try:
                        payload = json.loads(m.group(1))
                        payload["proc_i"] = i
                        event_callback(payload)
                    except json.JSONDecodeError:
                        print(f"decode error {m.group(1)}")
                        pass
                # else:
                #     line = self.ansi_escape.sub("", line)
                #     sys.stdout.write(f"{i} out: {line}")

            if ret_code is not None:
                done += [root_name]

        for root_name in done:
            p = self.procs[root_name]
            p.read_out.close()
            p.read_err.close()
            p.writ_out.close()
            p.writ_err.close()
            del self.procs[root_name]

        while len(self.procs) < self.n_workers:
            if not self._start_next():
                break

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
            root, include_dirs, allow_to_run.split(":"), match_string, exclude_string, bypass_skip
        )

        if len(errors) > 0:
            raise ZestRunnerErrors(errors)

        self.output_folder = output_folder
        self.n_workers = n_workers
        self.procs = {}
        self.queue = deque()

        for (root_name, (module_name, package, full_path)) in root_zests.items():
            self.queue.append((root_name, module_name, package, full_path))

        for i in range(self.n_workers):
            self._start_next()