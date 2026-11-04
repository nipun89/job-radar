"""Job Radar command line.

    python -m radar setup              first run: create config.yaml and the database, offer a daily schedule
    python -m radar serve              open the dashboard (http://localhost:5050)
    python -m radar check              fetch every company now (what the daily schedule runs)
    python -m radar schedule [HH:MM]   run the check every day (Windows Task Scheduler, macOS launchd, Linux cron)
    python -m radar schedule --remove  stop the daily check
    python -m radar demo               try it with made-up data (kept in data/demo/, port 5051)
"""
import argparse
import os
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                # so `import app` works from any folder
TASK_NAME = "JobRadar Daily"                 # Windows
LAUNCHD_LABEL = "com.jobradar.daily"         # macOS
CRON_TAG = "# job-radar daily check"         # Linux


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m radar", description="Job Radar: a local job search radar.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup", help="first run: create config.yaml and the database")
    p.add_argument("--schedule", metavar="HH:MM", help="also schedule the daily check at this time")
    p.add_argument("--no-schedule", action="store_true", help="don't ask about scheduling")
    p = sub.add_parser("serve", help="open the dashboard")
    p.add_argument("--port", type=int)
    p.add_argument("--no-browser", action="store_true")
    sub.add_parser("check", help="fetch every company now")
    p = sub.add_parser("schedule", help="run the check every day")
    p.add_argument("time", nargs="?", default="07:00", help="24-hour time, default 07:00")
    p.add_argument("--remove", action="store_true", help="remove the daily check")
    p = sub.add_parser("demo", help="try it with made-up data")
    p.add_argument("--port", type=int, default=5051)
    p.add_argument("--no-browser", action="store_true")
    p.add_argument("--reset", action="store_true", help="start the demo data over")
    args = parser.parse_args(argv)

    # These must be set before radar.db / app are imported: they read them once.
    if args.cmd == "demo":
        os.environ["JOB_RADAR_DEMO"] = "1"
    if getattr(args, "port", None):
        os.environ["JOB_RADAR_PORT"] = str(args.port)

    return {"setup": cmd_setup, "serve": cmd_serve, "check": cmd_check,
            "schedule": cmd_schedule, "demo": cmd_demo}[args.cmd](args)


# ---------- setup / serve / check / demo ----------

def cmd_setup(args):
    from radar import db
    if db.ensure_config():
        print(f"Created {db.CONFIG_PATH.name} from config.example.yaml. Edit it to set your roles and companies.")
    else:
        print(f"{db.CONFIG_PATH.name} already exists; left as is.")
    db.connect().close()
    print(f"Database ready at {db.DB_PATH.relative_to(ROOT)}")

    when = args.schedule
    if not when and not args.no_schedule and sys.stdin.isatty():
        answer = input("Run the check automatically every day at 7:00am? [Y/n] ").strip().lower()
        when = "07:00" if answer in ("", "y", "yes") else None
    if when:
        schedule(when)
    print("\nDone. Start the dashboard with:  python -m radar serve"
          + ("   (or double-click 'Job Radar.bat')" if os.name == "nt" else ""))
    return 0


def cmd_serve(args):
    import app
    app.serve(open_browser=not args.no_browser)
    return 0


def cmd_check(args):
    """Run a full check. Errors are written to logs/run.log, since a scheduled run has no window."""
    from radar import db
    from radar.run import format_summary, run_check
    try:
        print(format_summary(run_check()))
    except Exception:
        db.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(db.LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  FAILED\n{traceback.format_exc()}\n")
        raise
    after = (db.load_config().get("after_check") or "").strip()
    if after:
        # Optional hook from config.yaml, e.g. a backup script. Runs from the project folder.
        # A leading "python" means Job Radar's own Python (the virtual environment's).
        if after == "python" or after.startswith("python "):
            after = f'"{sys.executable}"' + after[len("python"):]
        print(f"after_check: {after}")
        code = subprocess.call(after, shell=True, cwd=ROOT)
        if code:
            print(f"after_check failed (exit code {code})", file=sys.stderr)
        return code
    return 0


def cmd_demo(args):
    from radar import db, demo
    if args.reset and db.DATA_DIR.exists():
        import shutil
        shutil.rmtree(db.DATA_DIR)
    db.ensure_config()
    conn = db.connect()
    if demo.seed(conn):
        print(f"Created demo data in {db.DATA_DIR.relative_to(ROOT)} (your real data is untouched).")
    conn.close()
    import app
    app.serve(open_browser=not args.no_browser)
    return 0


# ---------- schedule ----------

def cmd_schedule(args):
    if args.remove:
        unschedule()
    else:
        schedule(args.time)
    return 0


def _parse_time(text):
    try:
        t = datetime.strptime(text, "%H:%M")
    except ValueError:
        sys.exit(f"Time must look like 07:00 (24-hour), got {text!r}")
    return t.hour, t.minute


def schedule(when):
    hour, minute = _parse_time(when)
    py = sys.executable
    (ROOT / "logs").mkdir(exist_ok=True)
    if os.name == "nt":
        # PowerShell's scheduled-task cmdlets let us set the working folder and
        # "run as soon as possible if the PC was off at that time".
        script = f"""
$a = New-ScheduledTaskAction -Execute '{py}' -Argument '-m radar check' -WorkingDirectory '{ROOT}'
$t = New-ScheduledTaskTrigger -Daily -At '{hour:02d}:{minute:02d}'
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $a -Trigger $t -Settings $s -Description 'Job Radar daily check' -Force | Out-Null
"""
        _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])
        print(f"Scheduled '{TASK_NAME}' in Task Scheduler, daily at {hour:02d}:{minute:02d}. "
              "If the PC is off then, it runs when it's next on.")
    elif sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        log = ROOT / "logs" / "task_output.log"
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>-m</string><string>radar</string><string>check</string></array>
  <key>WorkingDirectory</key><string>{ROOT}</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""", encoding="utf-8")
        subprocess.call(["launchctl", "unload", str(plist)], stderr=subprocess.DEVNULL)
        _run(["launchctl", "load", str(plist)])
        print(f"Scheduled with launchd ({plist}), daily at {hour:02d}:{minute:02d}. "
              "If the Mac is asleep then, it runs when it wakes.")
    else:
        line = f'{minute} {hour} * * * cd "{ROOT}" && "{py}" -m radar check >> logs/task_output.log 2>&1 {CRON_TAG}'
        _set_crontab(_crontab_without_ours() + [line])
        print(f"Added to your crontab, daily at {hour:02d}:{minute:02d}. (cron skips runs while the machine is off.)")


def unschedule():
    if os.name == "nt":
        subprocess.call(["powershell", "-NoProfile", "-Command",
                         f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false"])
    elif sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        if plist.exists():
            subprocess.call(["launchctl", "unload", str(plist)])
            plist.unlink()
    else:
        _set_crontab(_crontab_without_ours())
    print("Daily check removed.")


def _crontab_without_ours():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = r.stdout.splitlines() if r.returncode == 0 else []
    return [l for l in lines if CRON_TAG not in l]


def _set_crontab(lines):
    subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=True)


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"Scheduling failed:\n{r.stderr or r.stdout}")


if __name__ == "__main__":
    sys.exit(main())
