#!/usr/bin/env python3
"""Build the current xige_foc firmware and update it through CAN-OTA."""

import os
import subprocess
import shutil
import sys


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    make = os.path.join(project_root, "tools", "make", "bin",
                        "make.exe" if os.name == "nt" else "make")
    if os.name != "nt":
        make = shutil.which("make") or os.path.join(project_root, "tools", "linux", "usr", "bin", "make")
    makefile = os.path.join(project_root, "Makefile")
    if not os.path.isfile(makefile):
        sys.stderr.write("xige_foc Makefile not found: %s\n" % makefile)
        return 2
    if not os.path.isfile(make):
        sys.stderr.write("Bundled make executable not found: %s\n" % make)
        return 2
    sys.stdout.write("Project: %s\nCommand: make ota-onekey\n" % project_root)
    sys.stdout.flush()
    env = os.environ.copy()
    if os.name == "nt":
        # Bundled GCC must use a writable directory, including when launched by the web service.
        build_tmp = os.path.join(project_root, "build", "tmp")
        os.makedirs(build_tmp, exist_ok=True)
        env.update(TMPDIR=build_tmp, TEMP=build_tmp, TMP=build_tmp)
    return subprocess.call([make, "ota-onekey"], cwd=project_root, env=env)


if __name__ == "__main__":
    sys.exit(main())
