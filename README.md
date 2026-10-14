# ef-core-skills

Four Claude Code skills for EF Core: add a migration, update the database, remove a migration, and scaffold from an existing database.

They wrap one cross-platform script, `ef_tool.py`. It replaces the original interactive PowerShell scripts (`Read-Host` prompts), which Claude cannot answer.

Requirements: the .NET SDK and Python 3.8 or newer. No extra Python packages.

---

## What it does

| Skill | Slash command | Who can start it | What it does |
|---|---|---|---|
| `ef-migration-add` | `/ef-migration-add` | You or Claude | Runs `dotnet ef migrations add`. Can also fill an empty migration with custom SQL (views, stored procedures, functions, triggers, data fixes). |
| `ef-database-update` | `/ef-database-update` | You only | Runs `dotnet ef database update`: to the latest migration, to a specific one, or back to `0`. |
| `ef-migration-remove` | `/ef-migration-remove` | You only | Runs `dotnet ef migrations remove`, after checking whether the last migration is already applied. |
| `ef-scaffold` | `/ef-scaffold` | You only | Runs `dotnet ef dbcontext scaffold`, then splits `OnModelCreating` into one `IEntityTypeConfiguration<T>` class per entity. |

"You only" means `disable-model-invocation: true` is set in that skill's `SKILL.md`. Claude will not run these on its own, and will not load their descriptions into its context.

For every skill, Claude:

1. Runs discovery: finds the DbContexts, the startup projects, and checks that `dotnet-ef` is installed.
2. Asks you in the chat when a choice is ambiguous (DbContext, startup project, migration name, target migration).
3. Shows the exact `dotnet ef` command as a dry run.
4. Waits for your confirmation.
5. Runs the same command with `--execute`.

---

## Why a skill (and not a command, subagent, hook, or MCP server)

| Option | Fit | Reason |
|---|---|---|
| **Skill** | Best | Bundles the `.py` file next to its instructions. Works as a `/slash-command`. |
| Command only | OK | A single markdown file cannot cleanly carry the script. |
| Subagent | Overkill | Meant for tasks that need their own context or judgment. The script is deterministic. |
| Hook / MCP server | Overkill | Too heavy for a set of developer helper commands. |

---

## Folder structure

```
.claude/skills/
├── README.md
├── ef-migration-add/
│   ├── SKILL.md
│   ├── scripts/ef_tool.py
│   └── references/troubleshooting.md
├── ef-database-update/
│   ├── SKILL.md
│   ├── scripts/ef_tool.py
│   └── references/troubleshooting.md
├── ef-migration-remove/
│   ├── SKILL.md
│   ├── scripts/ef_tool.py
│   └── references/troubleshooting.md
└── ef-scaffold/
    ├── SKILL.md
    ├── scripts/ef_tool.py
    └── references/troubleshooting.md
```

- Put it in `.claude/skills/` inside your repo to use it in that project only.
- Put the four `ef-*` folders in `~/.claude/skills/` to use them in all your projects.

Each skill folder has its own copy of `ef_tool.py` and `troubleshooting.md` so every skill is self-contained. The four copies are identical.

---

## Installation

1. Unzip the package at the root of your repo. This creates `.claude/skills/`.
2. Restart Claude Code (or start a new session).
3. Type `/` and check that the four `ef-*` commands appear in the list.
4. Make sure the .NET SDK is installed. If `dotnet-ef` is missing, Claude will ask before running `dotnet tool install --global dotnet-ef`.

On Windows, `python3` may not exist. The skills tell Claude to use whichever of `python3`, `python` or `py -3` is available, so no change is needed.

---

## Usage

### In Claude Code

```
/ef-migration-add AddOrderStatus
/ef-database-update
/ef-database-update InitialCreate
/ef-migration-remove
/ef-scaffold
```

You can also just ask: "add a migration for the new Order fields". Only `ef-migration-add` can be started this way.

**Add a migration.** Claude finds the DbContext and the startup project, asks for a migration name if you did not give one, and shows the command. If the migration comes out empty, it asks whether you wanted an empty migration for custom SQL. For SQL it asks for the UP script and the DOWN (rollback) script. A `GO` on its own line splits the script into separate `migrationBuilder.Sql()` calls.

**Update the database.** Claude lists the migrations as applied or pending and asks for the target (latest, a specific migration, or `0`). It asks you to confirm which database will be used. A rollback or `--target 0` needs a separate, explicit "yes".

**Remove a migration.** Claude names the last migration and checks whether it is applied. If it is, Claude offers two routes: revert first with `/ef-database-update` and the previous migration as the target (recommended), or `--force`, which reverts the migration in the database (runs its `Down()`) and then removes the files. `--force` is only used if you explicitly choose it.

**Scaffold.** Two modes:
- **A. Scaffold, then split** (default): `dotnet ef dbcontext scaffold`, then the configuration split.
- **B. Split only**: the DbContext was already scaffolded; only move its configuration into separate classes.

For the split you choose how configurations are applied in `OnModelCreating`:
- `--apply assembly`: one `ApplyConfigurationsFromAssembly(...)` call. New configuration classes are picked up automatically. Do not use it when several DbContexts live in the same assembly.
- `--apply each` (default): one `ApplyConfiguration(new XConfiguration())` line per entity. Safe with several contexts in one assembly.

### Running the script directly (without Claude)

```bash
# Find DbContexts, startup projects, and dotnet-ef status (read-only, prints JSON)
python ef_tool.py discover --root C:\src\MySolution

# List migrations as applied or pending (read-only)
python ef_tool.py migrations --context AppDbContext --project src/Data/Data.csproj --startup-project src/Web/Web.csproj

# Preview a command (this is the default)
python ef_tool.py ef add --name AddOrderStatus --context AppDbContext --project src/Data/Data.csproj --startup-project src/Web/Web.csproj

# Run it
python ef_tool.py ef add --name AddOrderStatus --context AppDbContext --project src/Data/Data.csproj --startup-project src/Web/Web.csproj --execute
```

### Script options

| Subcommand | Purpose |
|---|---|
| `discover` | Finds DbContexts and startup projects, and checks `dotnet-ef`. Options: `--root`, `--deep`. |
| `migrations` | Lists migrations as applied or pending. Options: `--context` (required), `--project`, `--startup-project`. |
| `ef add` | Options: `--name`, `--context`, `--project`, `--startup-project`, `--output-dir`. |
| `ef remove` | Options: `--context`, `--project`, `--startup-project`, `--force`. |
| `ef update` | Options: `--context`, `--project`, `--startup-project`, `--target` (a migration name, or `0`). |
| `ef scaffold` | Options: `--project`, `--startup-project`, `--connection` or `--connection-env`, `--provider`, `--context`, `--context-dir`, `--output-dir`, `--no-onconfiguring`, `--force`, `--extra`. |
| `add-sql` | Writes SQL into an empty migration. Options: `--migration-file`, `--up-sql-file`, `--down-sql-file`. |
| `split-config` | Options: `--context-file`, `--configurations-folder`, `--apply each\|assembly`, `--on-existing fail\|overwrite\|keep`, `--backup` / `--no-backup`, `--dry-run`. |

All `ef` commands are a **dry run unless `--execute` is given**.

Every call ends with one line, `EF_TOOL_RESULT: {json}`, so Claude can read the outcome reliably.

Exit codes: `0` ok, `1` error, `2` bad usage, `3` a decision is needed (see the result JSON).

---

## Safety model

- Commands are dry runs by default. `--execute` is only added after you confirm the command in the chat.
- `ef-database-update` and `ef-migration-remove` can only be started by you. Claude cannot run them on its own.
- Passwords in connection strings are masked in the displayed command. Prefer `Name=ConnectionStrings:...`, user-secrets, or an environment variable (`--connection-env`) so passwords never appear in the chat.
- No shell string building. Arguments are passed as a list, so quotes and spaces in names or connection strings are safe.
- For production databases, Claude suggests generating a script (`dotnet ef migrations script --idempotent`) so it can go through your normal deployment process.
- Claude never adds `--force` on its own. For `remove` it reverts the migration in the database; for `scaffold` it overwrites files.

---

## Example

**Sample dry run** (`/ef-migration-add AddOrderStatus`):

```
Command:
  dotnet ef migrations add AddOrderStatus --context AppDbContext --project src/Data/Data.csproj --startup-project src/Web/Web.csproj
Not executed (dry run). Re-run the same command with --execute after the user confirms.
EF_TOOL_RESULT: {"status": "dry-run", "command": "dotnet ef migrations add AddOrderStatus ...", "warnings": []}
```

After you confirm, Claude runs the same command with `--execute` and reports the migration file path. It also reports whether `Up()` is empty (`upIsEmpty`).

---

## CLAUDE.md file

You do **not** need to reference these skills in the `CLAUDE.md` file. Claude Code finds skills automatically from the `.claude/skills/` folder (project) or the `~/.claude/skills/` folder (personal).

Three of the four skills are manual-only on purpose, because they change a database or delete files. Keep it that way. Only `ef-migration-add` can be started by Claude, and it never applies anything to a database.

---

## Tips

- Commit or stash your changes first, so you can review the result with `git diff`.
- Always read the dry-run command before you confirm it.
- To reduce permission prompts, choose "always allow" in Claude Code for the read-only `discover` and `migrations` commands.
- The first `dotnet ef` run can be slow, because it builds the project.
- After a scaffold or split, build the solution. The configuration split is regex-based.
- Windows-only problems (for example Smart App Control blocking `dotnet-ef`) are covered in each skill's `references/troubleshooting.md`.

---

## Using it in claude.ai (not Claude Code)

Not recommended. These skills need your real solution, the .NET SDK, and a reachable database. Claude Code runs in your repo and has all three.

---

## Known limits

- `split-config` is regex-based, with string and comment awareness. It only understands the shape that `dotnet ef dbcontext scaffold` emits: `modelBuilder.Entity<T>(entity => { ... });`. Always build afterwards.
- Empty blocks, blocks whose class name duplicates an earlier entity, and unparsable blocks are left **in** `OnModelCreating`, and the result lists them. For duplicates, create that configuration by hand.
- If `<Entity>Configuration.cs` files already exist, the split stops with exit code 3. Claude then asks whether to overwrite or keep them.
- Everything else in `OnModelCreating` (`HasSequence`, `HasDefaultSchema`, `OnModelCreatingPartial(...)`) stays where it is.
- Line endings (CRLF or LF) are preserved. Files are written as UTF-8 without BOM.

---

## Maintaining

Each skill has its own copy of `ef_tool.py` and `troubleshooting.md`. If you change one, copy it into the other three skill folders so they stay identical.
