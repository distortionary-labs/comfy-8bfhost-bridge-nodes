import json
import os
import sys
import textwrap

import pytest

from comfy_8bfhost_bridge import host


def fake_host(tmp_path, body):
    """A stand-in for 8bf-host: a Python script run with this interpreter."""
    script = tmp_path / "fake_host.py"
    script.write_text("import sys, time\n" + textwrap.dedent(body))
    return [sys.executable, str(script)]


def test_configured_executable_wins():
    assert host.find_executable("/opt/8bf-host", which=lambda _name: "/usr/bin/8bf-host") == (
        "/opt/8bf-host"
    )


def test_environment_variable_comes_next():
    found = host.find_executable(
        "", which=lambda _name: "/usr/bin/8bf-host", environ={"EIGHTBF_HOST": "/srv/8bf-host"}
    )
    assert found == "/srv/8bf-host"


def test_executable_on_path():
    found = host.find_executable("", which=lambda _name: "/usr/bin/8bf-host", environ={})
    assert found == "/usr/bin/8bf-host"


def test_executable_from_cargo_install(tmp_path):
    binary = (
        tmp_path / ".cargo" / "bin" / ("8bf-host.exe" if sys.platform == "win32" else "8bf-host")
    )
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    found = host.find_executable("", which=lambda _name: None, home=str(tmp_path), environ={})
    assert found == str(binary)


def test_missing_executable(tmp_path):
    assert host.find_executable("", which=lambda _name: None, home=str(tmp_path), environ={}) == ""


def test_missing_executable_explains_the_download(monkeypatch):
    monkeypatch.setattr(host, "find_executable", lambda _configured, which: "")
    with pytest.raises(host.HostError, match="github.com/distortionary-labs/8bf-host.*my.json"):
        host.require_executable("", "my.json")


def test_configured_executable_must_exist(tmp_path):
    missing = str(tmp_path / "8bf-host")
    with pytest.raises(host.HostError, match="does not exist.*my.json"):
        host.require_executable(missing, "my.json", which=lambda _name: None)
    open(missing, "w").close()
    assert host.require_executable(missing, which=lambda _name: None) == missing
    # A bare program name is looked up on PATH.
    assert host.require_executable("8bf-host", which=lambda name: "/bin/" + name) == "8bf-host"


def test_run_command():
    command = host.run_command(
        "8bf-host",
        ["/a", "/b"],
        "1a2b3c4d5e6f",
        "in.pam",
        "out.pam",
        dialog=False,
        preset="p.json",
        save_preset="p.json",
        mask="mask.pgm",
        rect=(1, 2, 30, 40),
        foreground=(255, 128, 0),
        background=(0, 0, 0),
        resolution=300.0,
        allow_8bit_fallback=False,
    )
    assert command == [
        "8bf-host", "run", "--verbose",
        "--plugin-dir=/a", "--plugin-dir=/b",
        "--preset=p.json", "--save-preset=p.json",
        "--mask=mask.pgm", "--rect=1,2,30,40",
        "--foreground=#ff8000", "--background=#000000", "--resolution=300",
        "--no-8bit-fallback",
        "--", "1a2b3c4d5e6f", "in.pam", "out.pam",
    ]  # fmt: skip


def test_values_that_look_like_options_stay_values():
    command = host.run_command("8bf-host", ["--rescan"], "--help", "-in.pam", "-")
    assert "--plugin-dir=--rescan" in command
    assert command[-4:] == ["--", "--help", "-in.pam", "-"]


def test_run_command_defaults_show_the_dialog():
    command = host.run_command("8bf-host", [], "id", "in.pam", "out.pam")
    assert "--dialog" in command
    assert "--preset" not in command
    assert "--no-8bit-fallback" not in command


def test_list_command():
    assert host.list_command("h", ["/a"], rescan=True) == [
        "h", "list", "--json", "--plugin-dir=/a", "--rescan",
    ]  # fmt: skip


def test_successful_run_collects_output(tmp_path):
    command = fake_host(
        tmp_path,
        """
        print("hello")
        sys.stderr.write("[host] a log line\\n")
        """,
    )
    pumps = []
    result = host.run(command, pump=lambda: pumps.append(1))
    assert result.ok
    assert result.stdout == "hello\n"
    assert result.log == ["[host] a log line"]
    assert pumps


def test_failed_run_reports_the_error_line(tmp_path):
    command = fake_host(
        tmp_path,
        """
        sys.stderr.write("[host] details\\nerror: the filter failed on purpose\\nhint: x\\n")
        sys.exit(1)
        """,
    )
    result = host.run(command)
    assert result.status == "error"
    assert result.message == "the filter failed on purpose"
    assert "[host] details" in result.log


def test_crash_without_an_error_line(tmp_path):
    command = fake_host(tmp_path, "sys.stderr.write('Segmentation fault\\n'); sys.exit(139)")
    result = host.run(command)
    assert result.status == "error"
    assert "exited with code 139" in result.message
    assert "Segmentation fault" in result.message


def test_exit_code_130_is_a_cancel(tmp_path):
    assert host.run(fake_host(tmp_path, "sys.exit(130)")).status == "cancelled"


def test_program_that_cannot_start(tmp_path):
    result = host.run([str(tmp_path / "missing")])
    assert result.status == "error"
    assert "Could not start 8bf-host" in result.message


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no SIGINT for such processes")
def test_cancel_interrupts_the_host(tmp_path):
    ready = tmp_path / "ready"
    marker = tmp_path / "interrupted"
    command = fake_host(
        tmp_path,
        """
        import signal
        def stop(*_):
            open(%r, "w").close()
            sys.exit(130)
        signal.signal(signal.SIGINT, stop)
        open(%r, "w").close()
        time.sleep(30)
        """
        % (str(marker), str(ready)),
    )
    result = host.run(command, cancelled=ready.exists)
    assert result.status == "cancelled"
    assert marker.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="uses SIGINT")
def test_cancel_kills_a_host_that_ignores_it(tmp_path):
    command = fake_host(
        tmp_path,
        """
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        time.sleep(30)
        """,
    )
    result = host.run(command, cancelled=lambda: True, grace=0.3)
    assert result.status == "cancelled"


def test_list_filters_parses_json():
    payload = {"filters": [{"id": "abc", "name": "Invert"}], "problems": [{"path": "x"}]}

    def runner(command, timeout=None):
        assert command[1:3] == ["list", "--json"]
        assert timeout == host.LIST_TIMEOUT
        return host.Result("ok", stdout=json.dumps(payload))

    assert host.list_filters("h", [], runner=runner) == (payload["filters"], payload["problems"])


def test_list_filters_failures():
    def answer(status, stdout="", message=""):
        return lambda _command, timeout=None: host.Result(status, message, stdout)

    with pytest.raises(host.HostError, match="bad config"):
        host.list_filters("h", [], runner=answer("error", message="bad config"))
    with pytest.raises(host.HostError, match="could not list"):
        host.list_filters("h", [], runner=answer("cancelled"))
    with pytest.raises(host.HostError, match="not JSON"):
        host.list_filters("h", [], runner=answer("ok", "nope"))
    with pytest.raises(host.HostError, match="not a JSON object"):
        host.list_filters("h", [], runner=answer("ok", "[]"))
    with pytest.raises(host.HostError, match="'filters' that is not a list"):
        host.list_filters("h", [], runner=answer("ok", '{"filters": "x"}'))


def test_list_filters_skips_entries_that_are_not_objects():
    stdout = json.dumps({"filters": [1, {"id": "a"}], "problems": None})
    runner = lambda _command, timeout=None: host.Result("ok", stdout=stdout)
    assert host.list_filters("h", [], runner=runner) == ([{"id": "a"}], [])


@pytest.mark.skipif(sys.platform == "win32", reason="uses SIGINT")
def test_timeout_stops_the_host(tmp_path):
    result = host.run(fake_host(tmp_path, "time.sleep(30)"), timeout=0.2, grace=1.0)
    assert result.status == "error"
    assert "did not finish within 0.2 seconds" in result.message


def test_host_does_not_outlive_an_exception(tmp_path):
    pid_file = tmp_path / "pid"
    command = fake_host(
        tmp_path, "import os; open(%r, 'w').write(str(os.getpid())); time.sleep(30)" % str(pid_file)
    )

    def pump():
        if pid_file.exists() and pid_file.read_text():
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        host.run(command, pump=pump)
    pid = int(pid_file.read_text())
    if sys.platform != "win32":
        with pytest.raises(OSError):
            os.kill(pid, 0)


def test_run_uses_the_working_directory(tmp_path):
    command = fake_host(tmp_path, "import os; print(os.getcwd())")
    work = tmp_path / "work"
    work.mkdir()
    result = host.run(command, cwd=str(work))
    assert os.path.samefile(result.stdout.strip(), str(work))
