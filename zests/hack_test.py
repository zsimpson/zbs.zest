import os
import subprocess

print(f"start {os.getpid()=}", flush=True)
try:
    output = subprocess.check_output(
        f"python ./hack_death.py", shell=True, stderr=subprocess.STDOUT,
    )
    print(f"{output=}")
except subprocess.CalledProcessError as e:
    print("GOT ERROR", e)
