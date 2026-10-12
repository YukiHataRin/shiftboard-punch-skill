#!/usr/bin/env python3
"""Shiftboard time-tracking helper for the shiftboard-punch skill.

Uses only the Python standard library (Python 3.9+). Credentials are read from
config.json in the skill folder; never pass them on the command line.

Each working directory has its own state: the chosen project and, while a task
is in progress, the time that task started.

Commands:
  check                          verify config and login
  projects                       list projects you can log hours to
  start [--project ID]           start timing a task (project is remembered per directory)
        [--start HH:MM] [--note TEXT] [--force]
  status                         show the project and running task for this directory
  log --summary TEXT             log the running task as a time entry, then stop the task timer
      [--start HH:MM] [--end HH:MM] [--date YYYY-MM-DD] [--project ID] [--dry-run] [--yes]
  entries [--date YYYY-MM-DD]    list your own entries for a day (default today)
  stop                           stop the task timer without logging
  forget                         also forget the project chosen for this directory
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Taipei")
SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = SKILL_DIR / "config.json"
STATE_DIR = SKILL_DIR / "state"
DEFAULT_API = "https://api.shiftboard.fclinlab.com"
MAX_ENTRY_HOURS = 12
EXIT_NEEDS_CONFIRMATION = 3


class CliError(Exception):
    pass


# ---------- config & HTTP ----------

def load_config():
    if not CONFIG_PATH.exists():
        raise CliError(
            f"找不到設定檔 {CONFIG_PATH}\n"
            "請將 config.example.json 複製為 config.json，並填入自己的 Shiftboard 帳號與密碼。"
        )
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(f"config.json 不是有效的 JSON：{exc}") from exc
    missing = [k for k in ("username", "password") if not config.get(k)]
    if missing:
        raise CliError(f"config.json 缺少欄位：{', '.join(missing)}")
    if config["username"] == "your-username":
        raise CliError("config.json 仍是範例內容，請填入自己的帳號與密碼。")
    if os.name == "posix" and CONFIG_PATH.stat().st_mode & 0o077:
        print(f"提醒：{CONFIG_PATH} 其他使用者可讀取，建議執行 chmod 600。", file=sys.stderr)
    config["api_base_url"] = (config.get("api_base_url") or DEFAULT_API).rstrip("/")
    config["require_confirmation"] = bool(config.get("require_confirmation", False))
    return config


def request(config, method, path, token=None, json_body=None, form_body=None):
    url = config["api_base_url"] + path
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif form_body is not None:
        data = urllib.parse.urlencode(form_body).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        if exc.code == 401 and path == "/auth/login":
            raise CliError("登入失敗：帳號或密碼錯誤，請檢查 config.json。") from exc
        raise CliError(f"API {method} {path} 失敗（HTTP {exc.code}）：{detail}") from exc
    except urllib.error.URLError as exc:
        raise CliError(f"無法連線到 {config['api_base_url']}：{exc.reason}") from exc


class Session:
    def __init__(self, config):
        self.config = config
        result = request(config, "POST", "/auth/login",
                         form_body={"username": config["username"], "password": config["password"]})
        self.token = result["access_token"]
        self._me = None

    def get(self, path):
        return request(self.config, "GET", path, token=self.token)

    def post(self, path, body):
        return request(self.config, "POST", path, token=self.token, json_body=body)

    @property
    def me(self):
        if self._me is None:
            self._me = self.get("/users/me")
        return self._me

    def projects(self):
        return [p for p in self.get("/projects/") if p.get("is_active", True)]

    def my_entries(self):
        # Older servers ignore ?mine=true and return everyone's entries to admins,
        # so always filter by user id as well.
        entries = self.get("/time-entries/?mine=true")
        return [e for e in entries if e["user_id"] == self.me["id"]]


# ---------- per-directory state ----------

def state_path():
    key = hashlib.sha1(os.getcwd().encode("utf-8")).hexdigest()[:16]
    return STATE_DIR / f"{key}.json"


def load_state():
    path = state_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state):
    STATE_DIR.mkdir(exist_ok=True)
    state["cwd"] = os.getcwd()
    state_path().write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- time helpers ----------

def now():
    return datetime.now(TZ).replace(second=0, microsecond=0)


def parse_hhmm(value, day):
    try:
        return datetime.combine(day, time.fromisoformat(value), tzinfo=TZ)
    except ValueError as exc:
        raise CliError(f"時間格式需為 HH:MM：{value}") from exc


def parse_local(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def parse_api_datetime(value):
    # Python 3.9's fromisoformat does not accept a trailing "Z"
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("UTC"))
    return parsed.astimezone(TZ)


def fmt(dt):
    return dt.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def fmt_duration(delta):
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60} 小時 {minutes % 60} 分"


def find_project(projects, project_id):
    for project in projects:
        if project["id"] == project_id:
            return project
    raise CliError(f"找不到可登錄的計畫 id={project_id}，請先執行 projects 查看清單。")


def describe_entry(entry):
    start = parse_api_datetime(entry["start_time"])
    end = parse_api_datetime(entry["end_time"])
    return f"{fmt(start)}–{end:%H:%M}  {entry['project_name']}  {entry['description']}"


# ---------- commands ----------

def cmd_check(args):
    config = load_config()
    session = Session(config)
    me = session.me
    print(f"登入成功：{me['name']}（{me['username']}，角色 {me['role']}）")
    print(f"API：{config['api_base_url']}")
    print(f"送出前需要確認：{'是' if config['require_confirmation'] else '否'}")


def cmd_projects(args):
    session = Session(load_config())
    projects = session.projects()
    if not projects:
        print("目前沒有可登錄工時的計畫，請聯絡管理員將你加入計畫。")
        return
    current = load_state().get("project_id")
    for p in projects:
        desc = f" — {p['description']}" if p.get("description") else ""
        mark = "  ← 此目錄目前的計畫" if p["id"] == current else ""
        print(f"[{p['id']}] {p['name']}{desc}{mark}")


def cmd_start(args):
    state = load_state()
    if state.get("task_start") and not args.force:
        raise CliError(
            f"此目錄已有計時中的工作（開始於 {state['task_start']}，"
            f"內容：{state.get('task_note') or '未填'}）。請先 log 打卡或 stop，"
            "若確定要重新計時請加 --force。"
        )

    project_id = args.project or state.get("project_id")
    if not project_id:
        raise CliError("此目錄尚未選擇計畫，請先執行 projects 並用 --project 指定。")

    if args.project and args.project != state.get("project_id"):
        project = find_project(Session(load_config()).projects(), args.project)
        state["project_id"], state["project_name"] = project["id"], project["name"]

    start = parse_hhmm(args.start, now().date()) if args.start else now()
    if start > now():
        raise CliError(f"開始時間 {fmt(start)} 晚於現在。")
    state["task_start"] = fmt(start)
    state["task_note"] = args.note or ""
    save_state(state)
    print(f"開始計時：計畫「{state['project_name']}」，開始時間 {fmt(start)}")


def cmd_status(args):
    state = load_state()
    if not state.get("project_id"):
        print("此目錄尚未選擇計畫。")
        return
    print(f"計畫：{state['project_name']}（id {state['project_id']}）")
    if not state.get("task_start"):
        print("目前沒有計時中的工作。")
        return
    elapsed = now() - parse_local(state["task_start"])
    print(f"計時中：開始於 {state['task_start']}，已經過 {fmt_duration(elapsed)}")
    if state.get("task_note"):
        print(f"內容：{state['task_note']}")
    if elapsed > timedelta(hours=MAX_ENTRY_HOURS):
        print(f"警告：計時已超過 {MAX_ENTRY_HOURS} 小時，可能是上次忘記打卡，請確認實際開始時間。")


def cmd_entries(args):
    session = Session(load_config())
    day = date.fromisoformat(args.date) if args.date else now().date()
    rows = []
    for e in session.my_entries():
        start, end = parse_api_datetime(e["start_time"]), parse_api_datetime(e["end_time"])
        if start.date() <= day <= end.date():
            rows.append((start, e))
    if not rows:
        print(f"{day} 沒有工時紀錄。")
        return
    for _, e in sorted(rows, key=lambda r: r[0]):
        print(describe_entry(e))


def cmd_log(args):
    config = load_config()
    state = load_state()
    day = date.fromisoformat(args.date) if args.date else None

    if args.start:
        start = parse_hhmm(args.start, day or now().date())
    elif state.get("task_start"):
        start = parse_local(state["task_start"])
    else:
        raise CliError("此目錄沒有計時中的工作，請用 --start HH:MM 指定開始時間。")

    end = parse_hhmm(args.end, day or start.date()) if args.end else now()
    if end <= start:
        raise CliError(f"結束時間 {fmt(end)} 必須晚於開始時間 {fmt(start)}。")
    if end - start > timedelta(hours=MAX_ENTRY_HOURS):
        raise CliError(
            f"時段長達 {fmt_duration(end - start)}，超過 {MAX_ENTRY_HOURS} 小時，"
            "請確認實際工作時間後分段登錄。"
        )

    summary = " ".join(args.summary.split())
    if not summary:
        raise CliError("--summary 不可為空。")

    project_id = args.project or state.get("project_id")
    if not project_id:
        raise CliError("此目錄尚未選擇計畫，請用 --project 指定。")

    session = Session(config)
    project = find_project(session.projects(), project_id)

    print(f"計畫：{project['name']}")
    print(f"時段：{fmt(start)} → {fmt(end)}（{fmt_duration(end - start)}）")
    print(f"內容：{summary}")

    overlaps = [
        e for e in session.my_entries()
        if parse_api_datetime(e["start_time"]) < end and parse_api_datetime(e["end_time"]) > start
    ]
    if overlaps:
        lines = "\n".join(f"  {describe_entry(e)}" for e in overlaps)
        raise CliError(f"此時段與既有紀錄重疊，未送出：\n{lines}")

    if args.dry_run:
        print("（預覽模式，尚未送出）")
        return
    if config["require_confirmation"] and not args.yes:
        print("設定要求送出前確認：請讓使用者確認上述內容後，加上 --yes 重新執行。")
        sys.exit(EXIT_NEEDS_CONFIRMATION)

    entry = session.post("/time-entries/", {
        "project_id": project["id"],
        "description": summary,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
    })
    # Logging up to "now" closes the running task; a back-dated entry (--end) leaves it running
    if state.get("task_start") and not args.end:
        state.pop("task_start", None)
        state.pop("task_note", None)
        save_state(state)
    print(f"打卡完成（紀錄 id {entry['id']}）。")


def cmd_stop(args):
    state = load_state()
    if not state.get("task_start"):
        print("此目錄沒有計時中的工作。")
        return
    state.pop("task_start", None)
    state.pop("task_note", None)
    save_state(state)
    print("已停止計時，未送出任何紀錄。")


def cmd_forget(args):
    state_path().unlink(missing_ok=True)
    print("已清除此目錄的計畫與計時狀態。")


def main():
    parser = argparse.ArgumentParser(description="Shiftboard 工時打卡工具")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("projects")
    p = sub.add_parser("start")
    p.add_argument("--project", type=int, help="計畫 id（省略則沿用此目錄的計畫）")
    p.add_argument("--start", help="開始時間 HH:MM（預設現在）")
    p.add_argument("--note", help="這項工作的簡短說明")
    p.add_argument("--force", action="store_true", help="覆蓋計時中的工作")
    sub.add_parser("status")
    p = sub.add_parser("log")
    p.add_argument("--summary", required=True, help="詳細工作摘要")
    p.add_argument("--start", help="開始時間 HH:MM（預設使用 start 記錄的時間）")
    p.add_argument("--end", help="結束時間 HH:MM（預設現在）")
    p.add_argument("--date", help="--start/--end 所屬日期 YYYY-MM-DD")
    p.add_argument("--project", type=int, help="計畫 id（預設此目錄的計畫）")
    p.add_argument("--dry-run", action="store_true", help="只預覽，不送出")
    p.add_argument("--yes", action="store_true", help="使用者已確認（require_confirmation 時需要）")
    p = sub.add_parser("entries")
    p.add_argument("--date", help="YYYY-MM-DD（預設今天）")
    sub.add_parser("stop")
    sub.add_parser("forget")
    args = parser.parse_args()

    handlers = {
        "check": cmd_check, "projects": cmd_projects, "start": cmd_start,
        "status": cmd_status, "log": cmd_log, "entries": cmd_entries,
        "stop": cmd_stop, "forget": cmd_forget,
    }
    try:
        handlers[args.command](args)
    except CliError as exc:
        sys.stdout.flush()
        print(f"錯誤：{exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
