import sys
import os
import signal

print(f"{os.getpid()=}", flush=True)
os.kill(os.getpid(), signal.SIGKILL)
print(f"{os.getpid()=}", flush=True)

sys.exit(0)