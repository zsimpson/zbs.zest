from zest.zest_runner import ZestRunner
from zest.zest_console_ui import ZestConsoleUI

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose",
        default=1,
        type=int,
        help="0=silent, 1=dot-mode, 2=run-trace 3=full-trace",
    )
    parser.add_argument(
        "--include_dirs",
        nargs="?",
        default=os.getcwd(),
        help="Colon-delimited list of directories to search",
    )
    parser.add_argument(
        "--disable_shuffle",
        action="store_true",
        help="Disable the shuffling of test order",
    )
    parser.add_argument(
        "--add_markers", action="store_true", help="Used for internal debugging"
    )
    parser.add_argument(
        "--version", action="store_true", help="Show version and exit",
    )
    parser.add_argument(
        "match_string", type=str, nargs="?", help="Optional substring to match"
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="console UI",
    )
    parser.add_argument(
        "--n_workers",
        default=1,
        type=int,
        help="Number of parallel processes",
    )
    parser.add_argument(
        "--bypass_skip",
        nargs="?",
        default="",
        help="Colon-delimited list of skips to bypass. Do not use: only for self-testing.",
    )

    kwargs = vars(parser.parse_args())

    if kwargs.pop("version", None):
        print(__version__)
        sys.exit(0)

    if kwargs.pop("ui", False):
        runner_klass = ZestConsoleUI
    else:
        runner_klass = ZestRunner

    runner = runner_klass(**kwargs).run()
    sys.exit(runner.retcode)


if __name__ == "__main__":
    allow_reentrancy = False
    if allow_reentrancy:
        main()
    else:
        pidfile = f"{Path.home()}/zest_runner.pid"
        pid = str(os.getpid())
        if os.path.isfile(pidfile):
            print(f"{pidfile} already exists {sys.argv}", file=sys.stderr)
            sys.exit(1)

        with open(pidfile, 'w') as f:
            f.write(pid)

        try:
            main()
        finally:
            found_pid = 0
            with open(pidfile) as f:
                try:
                    found_pid = f.read()
                except Exception as e:
                    pass
            if str(found_pid) == str(pid):
                os.unlink(pidfile)
