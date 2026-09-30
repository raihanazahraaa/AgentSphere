"""Runs inside the sandboxed child process, before any model-written code.

Copied into the throwaway temp directory and executed there, so the child's sys.path[0] is
the temp directory rather than the AgentSphere package — model code cannot import the app.

Layers applied here, cheapest first:
  1. resource limits (CPU, address space, file size, no forking)
  2. network calls raise
  3. process spawning raises, so blocking sockets cannot be sidestepped via curl
  4. file access is confined to the temp working directory (reads of the interpreter's
     own library tree stay allowed, or `import json` would fail)
Then the target script is run with runpy, which keeps tracebacks pointing at the real
line numbers of the code the model wrote.

This is defence in depth on a single host, not a true jail. Anything running genuinely
untrusted code in production wants a container or VM with a seccomp profile; this stops
the accidents and the obvious escapes, which is what a school deployment needs.
"""

import os
import resource
import runpy
import sys

CPU_SECONDS = int(os.environ.get("SANDBOX_CPU_SECONDS", "5"))
ADDRESS_SPACE_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024


def _apply_resource_limits() -> None:
    # CPU time is the backstop for `while True: pass`, which no wall-clock timeout in the
    # parent can stop cleanly on its own.
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_FILE_BYTES, MAX_FILE_BYTES))
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
    except (ValueError, OSError):
        pass  # Not enforceable for this user on some systems; the spawn guards still apply.
    try:
        resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES))
    except (ValueError, OSError):
        # macOS often refuses RLIMIT_AS for the Python runtime; CPU and wall-clock remain.
        pass


def _block_network() -> None:
    import socket

    def _refuse(*_args, **_kwargs):
        raise PermissionError("Network access is disabled in this sandbox.")

    socket.socket = _refuse
    socket.create_connection = _refuse
    socket.create_server = _refuse
    for name in ("getaddrinfo", "gethostbyname"):
        setattr(socket, name, _refuse)


def _block_process_spawning() -> None:
    def _refuse(*_args, **_kwargs):
        raise PermissionError("Starting other programs is disabled in this sandbox.")

    os.system = _refuse
    os.popen = _refuse
    for name in dir(os):
        if name.startswith(("exec", "spawn", "fork", "posix_spawn")):
            try:
                setattr(os, name, _refuse)
            except (AttributeError, TypeError):
                pass

    import subprocess

    subprocess.Popen = _refuse
    subprocess.run = _refuse
    subprocess.call = _refuse
    subprocess.check_call = _refuse
    subprocess.check_output = _refuse


def _confine_filesystem(workdir: str) -> None:
    """Allow the workdir and the interpreter's own tree; deny everything else.

    Without this the snippet can read any file the API process can — including
    backend/.env and its JWT signing secret — and hand it back as an Observation, which
    lands straight in the model's context and then in the user's transcript.

    Reads under sys.prefix / sys.base_prefix have to stay open because `import json`
    reads the stdlib from disk.
    """
    import builtins
    import io

    real_open = builtins.open
    real_io_open = io.open
    real_os_open = os.open

    read_roots = [
        os.path.realpath(workdir),
        os.path.realpath(sys.base_prefix),
        os.path.realpath(sys.prefix),
        os.path.realpath(os.path.dirname(os.__file__)),
    ]
    write_roots = [os.path.realpath(workdir)]

    def _within(path, roots) -> bool:
        try:
            resolved = os.path.realpath(path)
        except (TypeError, ValueError, OSError):
            return False
        return any(resolved == root or resolved.startswith(root + os.sep) for root in roots)

    def _check(path, mode: str) -> None:
        # File descriptors are already-open handles the snippet legitimately holds.
        if isinstance(path, int):
            return
        try:
            path_str = os.fspath(path)
        except TypeError:
            raise PermissionError("Unsupported path type in this sandbox") from None
        if isinstance(path_str, bytes):
            path_str = path_str.decode("utf-8", "replace")

        writing = any(flag in mode for flag in ("w", "a", "x", "+"))
        roots = write_roots if writing else read_roots
        if not _within(path_str, roots):
            raise PermissionError(
                f"Access to '{path_str}' is not permitted; this sandbox can only use its "
                "own temporary folder."
            )

    def guarded_open(file, mode="r", *args, **kwargs):
        _check(file, mode)
        return real_open(file, mode, *args, **kwargs)

    def guarded_io_open(file, mode="r", *args, **kwargs):
        _check(file, mode)
        return real_io_open(file, mode, *args, **kwargs)

    def guarded_os_open(path, flags, *args, **kwargs):
        writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND))
        _check(path, "w" if writing else "r")
        return real_os_open(path, flags, *args, **kwargs)

    builtins.open = guarded_open
    io.open = guarded_io_open
    os.open = guarded_os_open

    # Mutating calls are confined to the workdir too, so a snippet cannot delete or
    # rename the user's files even though it cannot read them.
    for name in ("remove", "unlink", "rmdir", "mkdir", "makedirs", "rename", "replace"):
        original = getattr(os, name, None)
        if original is None:
            continue

        def make_guard(func):
            def guarded(path, *args, **kwargs):
                _check(path, "w")
                return func(path, *args, **kwargs)

            return guarded

        setattr(os, name, make_guard(original))


def main() -> int:
    if len(sys.argv) < 2:
        print("sandbox: no script given", file=sys.stderr)
        return 2

    target = os.path.realpath(sys.argv[1])
    _apply_resource_limits()
    _block_network()
    _block_process_spawning()
    # Last, so the guards above are installed before any snippet code can run, and so the
    # target script itself is still readable by runpy.
    _confine_filesystem(os.path.dirname(target))

    sys.argv = [target]
    try:
        runpy.run_path(target, run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
