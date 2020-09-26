
class ZestRunnerErrors(Exception):
    def __init__(self, errors):
        self.errors = errors


class ZestRunnerMultiThread:
    pat = re.compile(r"\@\@\@(.+)\@\@\@")
    ansi_escape = re.compile(r"(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]")

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

        n_done = 0
        for i, (p, fin_out, fin_err) in enumerate(zip(self.procs, self.fin_outs, self.fin_errs)):
            ret_code = p.poll()

            lines = fin_out.readlines()
            for line in lines:
                m = re.match(pat, line)
                if m:
                    try:
                        payload = json.loads(m.group(1))
                        event_callback(payload)
                    except json.JSONDecodeError:
                        pass
                else:
                    line = ansi_escape.sub("", line)
                    sys.stdout.write(f"{i} out: {line}")

            if ret_code is not None:
                n_done += 1

        if n_done == len(procs):
            return False

        return True

    def close(self):
        def close_list(fd_list):
            for fd in fd_list:
                close(fd)

        close_list(self.read_outs)
        close_list(self.read_errs)
        close_list(self.writ_outs)
        close_list(self.writ_errs)

        for proc in self.procs:
            proc.wait()

    def __init__(
        self,
        output_folder,
        root=None,
        include_dirs=None,
        allow_to_run="__all__",
        match_string=None,
        exclude_string=None,
        **kwargs,
    ):
        root_zests, allow_to_run, errors = zest_finder.find_zests(
            root, include_dirs, allow_to_run.split(":"), match_string, exclude_string, bypass_skip
        )

        if len(errors) > 0:
            raise ZestRunnerErrors(errors)

        self.procs = []
        self.read_outs = []
        self.read_errs = []
        self.writ_outs = []
        self.writ_errs = []

        for (root_name, (module_name, package, full_path)) in root_zests.items():
            out_path = os.path.join(output_folder, f"{root_name}.out")
            err_path = os.path.join(output_folder, f"{root_name}.err")

            writ_out = open(out_path, "w")
            writ_err = open(err_path, "w")
            self.writ_outs += [writ_out]
            self.writ_errs += [writ_err]
            self.read_outs += [open(f"out_{i}", "r")]
            self.read_errs += [open(f"err_{i}", "r")]
            self.procs += [
                Popen(
                    args=["python", "-u", "zest_shim.py", root_name, module_name, full_path],
                    bufsize=0,
                    executable="python",
                    stdin=DEVNULL,  # DEVNULL here prevents pudb from taking over
                    stdout=writ_out,
                    stderr=writ_err,
                )
            ]
