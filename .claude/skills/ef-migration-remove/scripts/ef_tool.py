#!/usr/bin/env python3
"""ef_tool.py - non-interactive EF Core helper used by the ef-* Claude Code skills.

Python port of the PowerShell EF Core migration scripts. Differences by design:
  * No prompts. Every decision is a command-line argument, so Claude (or a human)
    can drive it from any shell on Windows, macOS or Linux.
  * Standard library only. Needs Python 3.8+ and the .NET SDK (+ dotnet-ef).
  * Commands that change things are dry-run by default; add --execute to run.
  * The last line of output is always:  EF_TOOL_RESULT: {json}

Subcommands
  discover      find DbContexts, startup projects, dotnet-ef status        (read-only)
  migrations    list migrations for a context (applied / pending)          (read-only)
  ef            build and (optionally) run a dotnet ef command:
                  add | remove | update | scaffold
  add-sql       write pasted SQL into an EMPTY migration's Up()/Down()
  split-config  move modelBuilder.Entity<T>(...) blocks out of OnModelCreating
                into one IEntityTypeConfiguration<T> class per entity

Exit codes: 0 ok, 1 error, 2 bad usage, 3 a decision is needed (see result JSON).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

RESULT_PREFIX = "EF_TOOL_RESULT: "
SKIP_DIRS = {"bin", "obj", ".git", ".vs", ".idea", "node_modules"}

for _s in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------
def say(msg: str = "") -> None:
    print(msg, flush=True)


def finish(result: dict, code: int = 0) -> None:
    say(RESULT_PREFIX + json.dumps(result, ensure_ascii=False))
    sys.exit(code)


def fail(message: str, code: int = 1, **extra) -> None:
    say("ERROR: " + message)
    finish({"status": "error", "error": message, **extra}, code)


def read_text(path) -> str:
    """Read as UTF-8 (BOM tolerated) and keep the file's original line endings."""
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        return fh.read()


def write_text(path, text: str) -> None:
    """Write UTF-8 without BOM, no newline translation."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def walk_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def run_capture(args, cwd=None, timeout=None):
    try:
        p = subprocess.run(
            args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError:
        return 127, "", f"{args[0]}: command not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(args[:3])}: timed out"


def tail(text: str, n: int = 15) -> str:
    lines = [l for l in text.splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def display_command(args) -> str:
    """Printable command: passwords masked, args with special characters quoted."""
    shown = []
    for a in args:
        a = re.sub(r'(?i)((?:password|pwd)\s*=\s*)[^;"]+', r"\1********", a)
        a = re.sub(r"(://[^:/@\s]+:)[^@\s]+@", r"\1********@", a)
        if re.search(r"[\s;=\"'&|<>^$]", a):
            a = '"' + a.replace('"', '\\"') + '"'
        shown.append(a)
    return " ".join(shown)


# ----------------------------------------------------------------------------
# minimal C# source helpers (strings / chars / comments are skipped so braces
# or identifiers inside them are never mistaken for code)
# ----------------------------------------------------------------------------
CS_SKIP = (
    r'"""[\s\S]*?"""'                       # raw string literal
    r'|@"(?:[^"]|"")*"'                     # verbatim string
    r'|\$"(?:\\.|[^"\\\r\n])*"'             # interpolated string
    r'|"(?:\\.|[^"\\\r\n])*"'               # normal string
    r"|'(?:\\.|[^'\\\r\n])+'"               # char literal
    r"|//[^\r\n]*"                          # line comment
    r"|/\*[\s\S]*?\*/"                      # block comment
)
CS_BRACE_RE = re.compile(CS_SKIP + r"|[{}]")


def find_matching_brace(text: str, open_index: int) -> int:
    """Index of the '}' that closes the '{' at open_index, or -1."""
    depth = 0
    for m in CS_BRACE_RE.finditer(text, open_index):
        if len(m.group(0)) == 1:
            if m.group(0) == "{":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    return m.start()
    return -1


def rename_cs_identifier(text: str, old: str, new: str) -> str:
    """Rename an identifier in code only (never in strings/comments, never after '.')."""
    if old == new:
        return text
    pattern = re.compile("(" + CS_SKIP + r")|(?<![.\w])" + re.escape(old) + r"(?!\w)")
    return pattern.sub(lambda m: m.group(0) if m.group(1) is not None else new, text)


def format_cs_block_body(body: str, indent: str, newline: str):
    """Trim blank lines around a block body and re-indent it. None when empty."""
    lines = re.split(r"\r?\n", body)
    first = 0
    while first < len(lines) and lines[first].strip() == "":
        first += 1
    last = len(lines) - 1
    while last >= first and lines[last].strip() == "":
        last -= 1
    if last < first:
        return None
    lines = lines[first:last + 1]
    least = min(len(l) - len(l.lstrip()) for l in lines if l.strip() != "")
    out = ["" if l.strip() == "" else indent + l[least:].rstrip() for l in lines]
    return newline.join(out)


# ----------------------------------------------------------------------------
# discovery
# ----------------------------------------------------------------------------
# Handles: ': DbContext', ': IdentityDbContext<User>', ': BaseDbContext',
# generic classes and C# 12 primary constructors: class AppDbContext(DbContextOptions<AppDbContext> o) : DbContext(o)
DBCONTEXT_CLASS_RE = re.compile(
    r"\bclass\s+(\w+)\s*(?:<[^>]*>)?\s*(?:\([^)]*\))?\s*:\s*(?:[\w.]+\.)?\w*DbContext\b"
)
NAMESPACE_RE = re.compile(r"namespace\s+([A-Za-z0-9_.]+)\s*[{;]")


def csproj_for_file(file_path: Path):
    d = file_path.parent
    while True:
        found = sorted(d.glob("*.csproj"))
        if found:
            return found[0]
        if d.parent == d:
            return None
        d = d.parent


def iter_dbcontext_classes(text: str):
    """Yield (class_name, match) for non-abstract DbContext-derived classes."""
    for m in DBCONTEXT_CLASS_RE.finditer(text):
        before = text[max(0, m.start() - 40):m.start()]
        if re.search(r"\babstract\s+(?:partial\s+)?$", before):
            continue
        yield m.group(1), m


def find_dbcontexts(root: Path, deep: bool = False):
    results, seen = [], set()
    for f in walk_files(root):
        if f.suffix.lower() != ".cs":
            continue
        name_matches = re.search(r"Context\.cs$", f.name, re.I) is not None
        if not (name_matches or deep):
            continue
        text = read_text(f)
        if not text:
            continue
        nsm = list(NAMESPACE_RE.finditer(text))
        proj = csproj_for_file(f)
        classes = list(iter_dbcontext_classes(text))
        if not classes:
            if name_matches and not deep and not re.search(r"\babstract\s+class\b", text):
                cls = f.stem
                ns = nsm[0].group(1) if nsm else None
                results.append({
                    "name": cls, "fullName": f"{ns}.{cls}" if ns else cls,
                    "file": str(f), "project": str(proj) if proj else None,
                    "confirmed": False,
                })
            continue
        for cls, m in classes:
            ns = None
            for nm in nsm:
                if nm.start() < m.start():
                    ns = nm.group(1)
                else:
                    break
            key = (str(f), cls)
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "name": cls, "fullName": f"{ns}.{cls}" if ns else cls,
                "file": str(f), "project": str(proj) if proj else None,
                "confirmed": True,
            })
    # --context argument: fully-qualified name only when the short name is ambiguous
    counts = {}
    for r in results:
        counts[r["name"]] = counts.get(r["name"], 0) + 1
    per_project = {}
    for r in results:
        per_project[r["project"]] = per_project.get(r["project"], 0) + 1
    for r in results:
        r["nameArg"] = r["fullName"] if counts[r["name"]] > 1 else r["name"]
        r["otherContextsInSameProject"] = per_project[r["project"]] - 1
    return results


def find_projects(root: Path):
    projects, startup = [], []
    for p in walk_files(root):
        if p.suffix.lower() != ".csproj":
            continue
        content = read_text(p)
        is_test = "Microsoft.NET.Test.Sdk" in content
        entry = {"project": str(p), "name": p.stem, "isTestProject": is_test}
        projects.append(entry)
        is_web = re.search(r'Sdk\s*=\s*["\']Microsoft\.NET\.Sdk\.(?:Web|Worker)["\']', content)
        is_exe = re.search(r"<OutputType>\s*Exe\s*</OutputType>", content)
        has_program = (p.parent / "Program.cs").exists()
        if is_web or is_exe or has_program:
            startup.append({**entry, "kind": "web" if is_web else "exe"})
    return projects, startup


def ef_tool_status(root: Path) -> dict:
    rc, out, err = run_capture(["dotnet", "--version"], cwd=root, timeout=60)
    if rc == 127:
        return {"dotnetInstalled": False, "dotnetSdk": None, "installed": False, "version": None}
    sdk = out.strip().splitlines()[-1] if out.strip() else None
    rc, out, err = run_capture(["dotnet", "ef", "--version"], cwd=root, timeout=120)
    lines = [l.strip() for l in out.splitlines() if l.strip()]
    ok = rc == 0 and bool(lines)
    return {"dotnetInstalled": True, "dotnetSdk": sdk, "installed": ok,
            "version": lines[-1] if ok else None}


def cmd_discover(a):
    root = Path(a.root).resolve()
    if not root.is_dir():
        fail(f"Root folder not found: {root}")
    contexts = find_dbcontexts(root, deep=a.deep)
    projects, startup = find_projects(root)
    tool = ef_tool_status(root)
    say(f"Root: {root}")
    say(f"dotnet-ef: {'v' + tool['version'] if tool['installed'] else 'NOT installed / not on PATH'}")
    say(f"DbContexts: {len(contexts)}   startup candidates: {len(startup)}   projects: {len(projects)}")
    finish({"status": "ok", "root": str(root), "efTool": tool, "dbContexts": contexts,
            "startupProjects": startup, "projects": projects})


# ----------------------------------------------------------------------------
# dotnet ef command building / running
# ----------------------------------------------------------------------------
def ef_scope_args(context, project, startup):
    out = []
    if context:
        out += ["--context", context]
    if project:
        out += ["--project", project]
    if startup:
        out += ["--startup-project", startup]
    return out


def parse_migration_lines(raw: str):
    items = []
    for line in raw.splitlines():
        m = re.match(r"^(\d{14}_\S+?)(\s*\(Pending\))?$", line.strip())
        if m:
            items.append({"id": m.group(1), "pending": bool(m.group(2))})
    return items


def cmd_migrations(a):
    args = ["dotnet", "ef", "migrations", "list", "--no-color"] + ef_scope_args(a.context, a.project, a.startup_project)
    rc, out, err = run_capture(args, cwd=a.root)
    if rc != 0:
        say("Could not list migrations (the command failed).")
        finish({"status": "error", "error": "dotnet ef migrations list failed",
                "details": tail(err + "\n" + out, 20)}, 1)
    items = parse_migration_lines(out)
    for i, it in enumerate(items, 1):
        say(f"[{i}] {it['id']}{'  (pending)' if it['pending'] else '  (applied)'}")
    if not items:
        say("No migrations found for this context.")
    finish({"status": "ok", "migrations": items, "latest": items[-1]["id"] if items else None})


def build_ef_args(a):
    v = a.verb
    scope = ef_scope_args(a.context, a.project, a.startup_project)
    if v == "add":
        if not a.name or not a.context:
            fail("add needs --name and --context", 2)
        args = ["dotnet", "ef", "migrations", "add", a.name] + scope
        if a.output_dir:
            args += ["--output-dir", a.output_dir]
    elif v == "remove":
        if not a.context:
            fail("remove needs --context", 2)
        args = ["dotnet", "ef", "migrations", "remove"] + scope
        if a.force:
            args.append("--force")
    elif v == "update":
        if not a.context:
            fail("update needs --context", 2)
        args = ["dotnet", "ef", "database", "update"]
        if a.target:
            args.append(a.target)
        args += scope
    elif v == "scaffold":
        conn = a.connection
        if a.connection_env:
            conn = os.environ.get(a.connection_env)
            if not conn:
                fail(f"Environment variable {a.connection_env} is not set")
        if not conn or not a.provider or not a.project:
            fail("scaffold needs --connection (or --connection-env), --provider and --project", 2)
        args = ["dotnet", "ef", "dbcontext", "scaffold", conn, a.provider, "--project", a.project]
        if a.startup_project:
            args += ["--startup-project", a.startup_project]
        if a.context:
            args += ["--context", a.context]
        if a.output_dir:
            args += ["--output-dir", a.output_dir]
        if a.context_dir:
            args += ["--context-dir", a.context_dir]
        if a.force:
            args.append("--force")
        if a.no_onconfiguring:
            args.append("--no-onconfiguring")
        if a.extra:
            if os.name == "nt":   # POSIX parsing would swallow backslashes in Windows paths
                args += [x[1:-1] if len(x) > 1 and x[0] == x[-1] and x[0] in "\"'" else x
                         for x in shlex.split(a.extra, posix=False)]
            else:
                args += shlex.split(a.extra)
    else:
        fail(f"Unknown verb {v}", 2)
    return args


def find_new_migration_file(search_root: Path, name: str, since: float):
    pat = re.compile(r"^\d{14}_" + re.escape(name) + r"\.cs$")
    best = None
    for p in walk_files(search_root):
        if pat.match(p.name) and p.stat().st_mtime >= since:
            if best is None or p.stat().st_mtime > best.stat().st_mtime:
                best = p
    return best


def find_recent_context_files(project_dir: Path, since: float, name=None):
    found = []
    for p in walk_files(project_dir):
        if p.suffix.lower() != ".cs" or p.stat().st_mtime < since:
            continue
        if any(True for _ in iter_dbcontext_classes(read_text(p))):
            found.append(p)
    if name:
        named = [p for p in found if p.stem == name]
        if named:
            found = named
    return found


def cmd_ef(a):
    args = build_ef_args(a)
    shown = display_command(args)
    warnings = []
    if a.verb == "update" and a.target == "0":
        warnings.append("Target 0 reverts EVERY migration for this context (schema changes are dropped).")
    if a.verb == "update" and a.target and a.target != "0":
        warnings.append("If the target is not the latest migration, later migrations are rolled back.")
    if a.verb == "remove" and a.force:
        warnings.append("--force connects to the database and, if the migration is applied, REVERTS it "
                        "(runs its Down()) before removing the files. Data in affected tables/columns can be lost.")
    if a.verb == "scaffold" and a.force:
        warnings.append("--force overwrites existing generated files.")
    say("Command:")
    say("  " + shown)
    for w in warnings:
        say("WARNING: " + w)
    if not a.execute:
        say("Not executed (dry run). Re-run the same command with --execute after the user confirms.")
        finish({"status": "dry-run", "command": shown, "warnings": warnings})

    if not shutil.which("dotnet"):
        fail("'dotnet' was not found on PATH. Install the .NET SDK, then install the EF tool with: "
             "dotnet tool install --global dotnet-ef")
    started = time.time()
    rc = subprocess.run(args, cwd=a.root).returncode
    if rc != 0:
        hint = None
        if a.verb == "scaffold":
            hint = ("Common causes: Microsoft.EntityFrameworkCore.Design or the provider package is not "
                    "referenced by the project/startup project, the connection string is wrong, or the build failed.")
        say(f"dotnet ef failed (exit code {rc}). See the output above. Nothing else was changed.")
        finish({"status": "error", "exitCode": rc, "command": shown, "hint": hint}, 1)

    result = {"status": "ok", "command": shown, "warnings": warnings}
    if a.verb == "add":
        search_root = Path(a.project).parent if a.project else Path(a.root)
        mig = find_new_migration_file(search_root, a.name, started - 5)
        if mig:
            text = read_text(mig)
            up = method_info(text, "Up")
            result["migrationFile"] = str(mig)
            result["upIsEmpty"] = bool(up and up["empty"])
            say(f"Migration file: {mig}")
            if result["upIsEmpty"]:
                say("NOTE: Up() is empty - EF found no model changes.")
        else:
            say("Could not locate the new migration file.")
            result["migrationFile"] = None
    elif a.verb == "scaffold":
        project_dir = Path(a.project).parent
        files = find_recent_context_files(project_dir, started - 5, a.context)
        result["generatedContextFiles"] = [str(p) for p in files]
        for p in files:
            say(f"Generated DbContext: {p}")
    finish(result)


# ----------------------------------------------------------------------------
# custom SQL in an empty migration
# ----------------------------------------------------------------------------
def method_info(text: str, method: str):
    m = re.search(
        r"(?m)^([ \t]*)(?:protected|public)\s+override\s+void\s+" + method +
        r"\s*\(\s*MigrationBuilder\s+(\w+)\s*\)\s*\{", text)
    if not m:
        return None
    open_i = m.end() - 1
    close_i = find_matching_brace(text, open_i)
    if close_i < 0:
        return None
    body = text[open_i + 1:close_i]
    stripped = re.sub(r"//[^\r\n]*|/\*[\s\S]*?\*/", "", body)
    return {"indent": m.group(1), "builder": m.group(2), "open": open_i,
            "close": close_i, "empty": stripped.strip() == ""}


def split_sql_batches(sql: str):
    """Split on lines containing only GO. Each batch becomes its own Sql() call."""
    batches, current = [], []
    for line in re.split(r"\r?\n", sql):
        if re.match(r"^\s*GO\s*$", line, re.I):
            chunk = "\n".join(current).strip("\r\n")
            if chunk.strip():
                batches.append(chunk)
            current = []
        else:
            current.append(line)
    chunk = "\n".join(current).strip("\r\n")
    if chunk.strip():
        batches.append(chunk)
    return batches


def sql_statements(sql: str, builder: str, indent: str, nl: str) -> str:
    out = []
    for batch in split_sql_batches(sql):
        escaped = re.sub(r"\r?\n", nl, batch.replace('"', '""'))
        out.append(f'{indent}{builder}.Sql(@"{nl}{escaped}{nl}");')
    return (nl + nl).join(out)


def read_sql_arg(path: str) -> str:
    text = sys.stdin.read() if path == "-" else read_text(path)
    return text.strip("\r\n")


def cmd_add_sql(a):
    mig = Path(a.migration_file)
    if not mig.is_file():
        fail(f"Migration file not found: {mig}")
    text = read_text(mig)
    nl = "\r\n" if "\r\n" in text else "\n"
    up = method_info(text, "Up")
    if not up:
        fail("Could not find Up(MigrationBuilder ...) in the migration file; left untouched.")
    if not up["empty"]:
        fail("Up() already contains code (model changes). Custom SQL was NOT added. "
             "Edit the file by hand if you really want to add SQL to it.")
    up_sql = read_sql_arg(a.up_sql_file)
    if not up_sql.strip():
        fail("The UP SQL is empty.")
    down_sql = read_sql_arg(a.down_sql_file) if a.down_sql_file else ""
    if down_sql.strip() and down_sql == up_sql:
        fail("The DOWN SQL is identical to the UP SQL. Provide the rollback script for --down-sql-file.")

    notes, down_written = [], False
    down = method_info(text, "Down")
    # Down() sits below Up(), so edit it first and the Up() offsets stay valid.
    if down_sql.strip():
        if down and down["empty"]:
            stmts = sql_statements(down_sql, down["builder"], down["indent"] + "    ", nl)
            text = text[:down["open"] + 1] + nl + stmts + nl + down["indent"] + text[down["close"]:]
            down_written = True
        else:
            notes.append("Down() is missing or not empty - the rollback SQL was NOT added.")
    stmts = sql_statements(up_sql, up["builder"], up["indent"] + "    ", nl)
    text = text[:up["open"] + 1] + nl + stmts + nl + up["indent"] + text[up["close"]:]
    write_text(mig, text)
    say(f"SQL written to {mig}")
    if not down_written and not notes:
        notes.append("Down() was left empty, so this migration cannot be rolled back.")
    for n in notes:
        say("NOTE: " + n)
    finish({"status": "ok", "migrationFile": str(mig),
            "upBatches": len(split_sql_batches(up_sql)),
            "downWritten": down_written, "notes": notes})


# ----------------------------------------------------------------------------
# split entity configurations out of OnModelCreating
# ----------------------------------------------------------------------------
def configuration_source(usings, namespace, file_scoped, entity_type, class_name, body, nl) -> str:
    L = list(usings) + [""]
    if file_scoped:
        L += [f"namespace {namespace};", ""]
        i = ""
    else:
        L += [f"namespace {namespace}", "{"]
        i = "    "
    L += [
        f"{i}public class {class_name}Configuration : IEntityTypeConfiguration<{entity_type}>",
        f"{i}{{",
        f"{i}    public void Configure(EntityTypeBuilder<{entity_type}> builder)",
        f"{i}    {{",
        body,
        f"{i}    }}",
        f"{i}}}",
    ]
    if not file_scoped:
        L.append("}")
    return nl.join(L) + nl


def cmd_split_config(a):
    ctx_file = Path(a.context_file)
    if not ctx_file.is_file():
        fail(f"File not found: {ctx_file}")
    text = read_text(ctx_file)
    nl = "\r\n" if "\r\n" in text else "\n"

    omc = re.search(
        r"protected\s+override\s+void\s+OnModelCreating\s*\(\s*ModelBuilder\s+(\w+)\s*\)\s*\{", text)
    if not omc:
        fail("Could not find 'protected override void OnModelCreating(ModelBuilder ...)' in this file.")
    builder_var = omc.group(1)
    body_open = omc.end() - 1
    body_close = find_matching_brace(text, body_open)
    if body_close < 0:
        fail("Could not find the end of OnModelCreating (unbalanced braces?).")

    nsm = re.search(r"(?m)^\s*namespace\s+([\w.]+)\s*([;{])", text)
    ctx_ns = nsm.group(1) if nsm else None
    file_scoped = (nsm.group(2) == ";") if nsm else True
    folder = a.configurations_folder
    folder_ns = re.sub(r"[\\/]+", ".", folder).strip(".")
    config_ns = f"{ctx_ns}.{folder_ns}" if ctx_ns else folder_ns
    body_indent = " " * (8 if file_scoped else 12)
    cm = re.search(r"\bclass\s+(\w+)", text)
    ctx_class = cm.group(1) if cm else "DbContext"

    ent_re = re.compile(
        r"\b" + re.escape(builder_var) +
        r"\s*\.\s*Entity\s*<\s*([\w.]+)\s*>\s*\(\s*(\w+)\s*=>\s*\{")
    tail_re = re.compile(r"\s*\)\s*;")
    blocks, unparsed = [], []
    pos = body_open + 1
    while True:
        m = ent_re.search(text, pos)
        if not m or m.start() >= body_close:
            break
        open_i = m.end() - 1
        close_i = find_matching_brace(text, open_i)
        if close_i < 0:
            break
        tail_m = tail_re.match(text, close_i + 1)
        if not tail_m:
            unparsed.append(m.group(1))
            pos = close_i + 1
            continue
        line_start = text.rfind("\n", 0, m.start()) + 1
        prefix = text[line_start:m.start()]
        entity_type, param = m.group(1), m.group(2)
        inner = text[open_i + 1:close_i]
        renamed = rename_cs_identifier(inner, param, "builder")
        blocks.append({
            "entity": entity_type,
            "cls": entity_type.split(".")[-1],
            "body": format_cs_block_body(renamed, body_indent, nl),
            "start": line_start if prefix.strip() == "" else m.start(),
            "end": tail_m.end(),
            "indent": prefix if prefix.strip() == "" else " " * 8,
            "remove": False,
        })
        pos = tail_m.end()

    if not blocks:
        say("No 'modelBuilder.Entity<T>(entity => { ... });' blocks found - nothing to extract.")
        say("(Already converted, or the scaffold used data annotations only.)")
        finish({"status": "nothing-to-extract", "contextFile": str(ctx_file), "unparsed": unparsed})

    converted, skipped_empty, skipped_dup, seen = [], [], [], set()
    for b in blocks:
        if not b["body"]:
            skipped_empty.append(b["cls"])
        elif b["cls"] in seen:
            skipped_dup.append(b["entity"])
        else:
            seen.add(b["cls"])
            b["remove"] = True      # only blocks that become a class are removed
            converted.append(b)

    config_dir = ctx_file.parent / folder
    existing = [b["cls"] for b in converted if (config_dir / f"{b['cls']}Configuration.cs").exists()]

    say(f"Found {len(blocks)} entity block(s); {len(converted)} will become configuration classes in:")
    say(f"  {config_dir}  (namespace {config_ns})")
    for b in converted:
        say(f"  + {b['cls']}Configuration.cs" + ("   [already exists]" if b["cls"] in existing else ""))
    for c in skipped_empty:
        say(f"  {c}: empty configuration - left untouched")
    for e in skipped_dup:
        say(f"  {e}: another entity with the same class name was already processed - left in OnModelCreating")
    for e in unparsed:
        say(f"  {e}: unexpected syntax after the closing brace - left untouched")

    plan = {
        "contextFile": str(ctx_file), "configurationsDirectory": str(config_dir),
        "namespace": config_ns, "wouldCreate": [f"{b['cls']}Configuration.cs" for b in converted],
        "alreadyExisting": [f"{c}Configuration.cs" for c in existing],
        "skippedEmpty": skipped_empty, "skippedDuplicateClassName": skipped_dup, "unparsed": unparsed,
    }
    if not converted:
        finish({"status": "nothing-to-extract", **plan})
    if a.dry_run:
        say("Dry run - nothing was changed.")
        finish({"status": "dry-run", **plan})
    if existing and a.on_existing == "fail":
        say(f"{len(existing)} configuration file(s) already exist. Nothing was changed. "
            "Re-run with --on-existing overwrite or --on-existing keep.")
        finish({"status": "needs-decision", "reason": "existing-configuration-files", **plan}, 3)
    overwrite = a.on_existing == "overwrite"

    # usings for the generated files
    usings = [m.group(0).strip() for m in re.finditer(r"(?m)^using\s+[^;=(]+;", text)]
    for req in ("using Microsoft.EntityFrameworkCore;",
                "using Microsoft.EntityFrameworkCore.Metadata.Builders;"):
        if req not in usings:
            usings.append(req)
    usings.sort(key=lambda u: (0 if re.match(r"^using System[;.]", u) else 1, u.rstrip(";").lower()))

    config_dir.mkdir(parents=True, exist_ok=True)
    written, kept = [], []
    for b in converted:
        path = config_dir / f"{b['cls']}Configuration.cs"
        if path.exists() and not overwrite:
            kept.append(str(path))
            continue
        write_text(path, configuration_source(
            usings, config_ns, file_scoped, b["entity"], b["cls"], b["body"], nl))
        written.append(str(path))

    # rewrite OnModelCreating
    apply_lines = []
    if a.apply == "assembly":
        if re.search(r"ApplyConfigurationsFromAssembly\s*\(", text):
            say("OnModelCreating already calls ApplyConfigurationsFromAssembly - not adding a second call.")
        else:
            apply_lines.append(f"{builder_var}.ApplyConfigurationsFromAssembly(typeof({ctx_class}).Assembly);")
    else:
        apply_lines = [f"{builder_var}.ApplyConfiguration(new {b['cls']}Configuration());" for b in converted]

    out, cursor, inserted = [], body_open + 1, False
    for b in blocks:
        if not b["remove"]:                       # keep untouched
            out.append(text[cursor:b["end"]])
            cursor = b["end"]
            continue
        between = text[cursor:b["start"]]
        if not inserted:
            out.append(between)
            if apply_lines:
                out.append(nl.join(b["indent"] + l for l in apply_lines))
            inserted = True
        elif between.strip():
            out.append(between)                   # another statement sat between two blocks
        cursor = b["end"]
    out.append(text[cursor:body_close])

    cleaned, prev_blank = [], False
    for ln in re.split(r"\r?\n", "".join(out)):
        blank = ln.strip() == ""
        if blank and prev_blank:
            continue
        cleaned.append(ln)
        prev_blank = blank
    new_text = text[:body_open + 1] + nl.join(cleaned) + text[body_close:]

    if a.apply == "each" and ctx_ns and not re.search(
            r"(?m)^using\s+" + re.escape(config_ns) + r"\s*;", new_text):
        using_line = f"using {config_ns};{nl}"
        ums = list(re.finditer(r"(?m)^using\s+[^;=(]+;[ \t]*\r?\n", new_text))
        if ums:
            last = ums[-1]
            new_text = new_text[:last.end()] + using_line + new_text[last.end():]
        else:
            new_text = using_line + nl + new_text

    backup = None
    if a.backup:
        backup = str(ctx_file) + ".bak"
        write_text(backup, read_text(ctx_file))
        say(f"Backup of the original context: {backup}")
    write_text(ctx_file, new_text)
    say(f"Wrote {len(written)} configuration file(s) and updated {ctx_file.name}.")
    say("Next: build the project to verify the generated files compile.")
    finish({"status": "ok", **plan, "filesWritten": written, "filesKept": kept, "backup": backup})


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog="ef_tool.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="find DbContexts / startup projects / dotnet-ef")
    d.add_argument("--root", default=".", help="solution or repo folder (default: current dir)")
    d.add_argument("--deep", action="store_true",
                   help="scan every .cs file, not only *Context.cs (slower)")
    d.set_defaults(fn=cmd_discover)

    m = sub.add_parser("migrations", help="list migrations (applied / pending)")
    m.add_argument("--root", default=".")
    m.add_argument("--context", required=True)
    m.add_argument("--project")
    m.add_argument("--startup-project")
    m.set_defaults(fn=cmd_migrations)

    e = sub.add_parser("ef", help="build / run a dotnet ef command (dry run unless --execute)")
    e.add_argument("verb", choices=["add", "remove", "update", "scaffold"])
    e.add_argument("--root", default=".")
    e.add_argument("--context", help="DbContext name; for scaffold: the class name to generate")
    e.add_argument("--project")
    e.add_argument("--startup-project")
    e.add_argument("--name", help="add: migration name")
    e.add_argument("--output-dir")
    e.add_argument("--force", action="store_true", help="remove/scaffold: pass --force")
    e.add_argument("--target", help="update: migration name, or 0 to revert all (default: latest)")
    e.add_argument("--connection", help="scaffold: connection string or Name=ConnectionStrings:X")
    e.add_argument("--connection-env", help="scaffold: read the connection string from this env var")
    e.add_argument("--provider", help="scaffold: provider package, e.g. Microsoft.EntityFrameworkCore.SqlServer")
    e.add_argument("--context-dir", help="scaffold: DbContext output folder")
    e.add_argument("--no-onconfiguring", action="store_true")
    e.add_argument("--extra", help='scaffold: extra options as one string, e.g. "--table Documents --no-pluralize"')
    e.add_argument("--execute", action="store_true", help="actually run it (default is a dry run)")
    e.set_defaults(fn=cmd_ef)

    s = sub.add_parser("add-sql", help="write SQL into an empty migration")
    s.add_argument("--migration-file", required=True)
    s.add_argument("--up-sql-file", required=True, help="path to a .sql file, or - for stdin")
    s.add_argument("--down-sql-file", help="optional rollback script")
    s.set_defaults(fn=cmd_add_sql)

    c = sub.add_parser("split-config", help="split OnModelCreating into IEntityTypeConfiguration classes")
    c.add_argument("--context-file", required=True)
    c.add_argument("--configurations-folder", default="Configurations")
    c.add_argument("--apply", choices=["each", "assembly"], default="each",
                   help="each: ApplyConfiguration(new XConfiguration()) per entity (safe default); "
                        "assembly: one ApplyConfigurationsFromAssembly call")
    c.add_argument("--on-existing", choices=["fail", "overwrite", "keep"], default="fail",
                   help="what to do when <Entity>Configuration.cs already exists (default: fail, exit 3)")
    c.add_argument("--backup", dest="backup", action="store_true", default=True)
    c.add_argument("--no-backup", dest="backup", action="store_false")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(fn=cmd_split_config)
    return p


def main():
    args = build_parser().parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
