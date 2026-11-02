#!/usr/bin/env python3
"""Build the current FOC_7010 firmware and update it through CAN-OTA."""

import os
import shutil
import subprocess
import sys


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundled_make = os.path.join(project_root, "tools", "make", "bin",
                               "make.exe" if os.name == "nt" else "make")
    make = os.environ.get("FOC_MAKE") or (
        bundled_make if os.path.isfile(bundled_make) else shutil.which("make"))
    makefile = os.path.join(project_root, "Makefile")
    if not os.path.isfile(makefile):
        sys.stderr.write("FOC_7010 Makefile not found: %s\n" % makefile)
        return 2
    if not make or not shutil.which(make):
        sys.stderr.write("GNU make not found; set FOC_MAKE to its executable.\n")
        return 2
    sys.stdout.write("Project: %s\nCommand: make ota-onekey\n" % project_root)
    sys.stdout.flush()
    env = os.environ.copy()
    if os.name != "nt":
        env.setdefault("PYTHON", sys.executable)
    if os.name == "nt":
        # Bundled GCC must use a writable directory, including when launched by the web service.
        build_tmp = os.path.join(project_root, "build", "tmp")
        os.makedirs(build_tmp, exist_ok=True)
        env.update(TMPDIR=build_tmp, TEMP=build_tmp, TMP=build_tmp)
    return subprocess.call([make, "ota-onekey"], cwd=project_root, env=env)


if __name__ == "__main__":
    sys.exit(main())
