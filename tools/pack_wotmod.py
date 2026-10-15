"""Pack the client mod into a ``.wotmod`` (a zip) for ``mods/<version>/``.

The client mod is shipped as Python 2.7 **sources** inside
``client_mod/res_mods/<version>/`` (with the mod loader overridden so a release
client loads ``mod_*.py`` as well as ``mod_*.pyc``).  WoT 0.9.22 also accepts a
mod package: a zip named ``mods/0.9.22.0.1/<something>.wotmod`` that contains
the same ``res/`` tree.

By default this tool copies the sources into the archive, which the bundled
loader override can import.  If a Python 2.7 interpreter is available it can
compile them first (``--python2``), which produces the ``.pyc`` files a stock,
unmodified client loads - keep in mind that only Python **2.7** produces the
bytecode 0.9.22 understands (magic ``\\x03\\xf3\\r\\n``).

Usage::

    python3 tools/pack_wotmod.py
    python3 tools/pack_wotmod.py --out dist/wotny.wotmod
    python3 tools/pack_wotmod.py --python2 C:/Python27/python.exe --compiled-only
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD_ROOT = os.path.join(REPO_ROOT, "client_mod", "res_mods")
VERSION = "0.9.22.0.1"
PY2_MAGIC = b"\x03\xf3\r\n"


def _tree_files(root: str):
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in ("__pycache__",)]
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                continue
            path = os.path.join(current, name)
            yield path, os.path.relpath(path, root)


def compile_with_python2(python: str, root: str) -> bool:
    """Compile every ``.py`` under ``root`` in place; ``True`` on success."""
    script = ("import compileall, sys; "
              "sys.exit(0 if compileall.compile_dir(sys.argv[1], quiet=1, force=1) else 1)")
    result = subprocess.call([python, "-c", script, root])
    return result == 0


def check_magic(root: str) -> bool:
    for current, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".pyc"):
                with open(os.path.join(current, name), "rb") as handle:
                    return handle.read(4) == PY2_MAGIC
    return False


def pack(out_path: str, python2: str = None, compiled_only: bool = False) -> str:
    version_root = os.path.join(MOD_ROOT, VERSION)
    if not os.path.isdir(version_root):
        raise SystemExit("the mod tree is missing: %s" % version_root)
    staging = tempfile.mkdtemp(prefix="wotny-wotmod-")
    try:
        root = os.path.join(staging, "res")
        shutil.copytree(os.path.join(version_root, "scripts"),
                        os.path.join(root, "scripts"))
        if python2:
            if not compile_with_python2(python2, root):
                raise SystemExit("%s could not compile the mod" % python2)
            sys.stdout.write("compiled with %s (magic ok: %s)\n"
                             % (python2, check_magic(root)))
        if compiled_only:
            for current, _dirs, files in os.walk(root):
                for name in files:
                    if name.endswith(".py"):
                        os.unlink(os.path.join(current, name))
        directory = os.path.dirname(os.path.abspath(out_path))
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path, relative in _tree_files(root):
                archive.write(path, os.path.join("res", relative).replace("\\", "/"))
        return out_path
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=os.path.join(REPO_ROOT, "dist", "wotny_0.9.22.0.1.wotmod"),
                        help="the package to write (default: dist/wotny_0.9.22.0.1.wotmod)")
    parser.add_argument("--python2", help="a Python 2.7 interpreter to compile the sources with")
    parser.add_argument("--compiled-only", action="store_true",
                        help="ship only the .pyc files (needs --python2)")
    args = parser.parse_args(argv)
    if args.compiled_only and not args.python2:
        raise SystemExit("--compiled-only needs --python2")
    target = pack(args.out, args.python2, args.compiled_only)
    print("wrote %s (%d KiB)" % (target, os.path.getsize(target) // 1024))
    print("install it as: <World_of_Tanks>/mods/%s/%s"
          % (VERSION, os.path.basename(target)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
