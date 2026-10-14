---
name: ef-scaffold
description: Database-first EF Core scaffolding. Runs dotnet ef dbcontext scaffold from an existing database, then splits the generated OnModelCreating into one IEntityTypeConfiguration<T> class per entity. Can also split the configurations of an already scaffolded DbContext. Shows every command before running it and masks passwords. Invoke manually with /ef-scaffold.
disable-model-invocation: true
---

# Scaffold from a database and split entity configurations

Helper script: `scripts/ef_tool.py` in this skill's folder. It needs Python 3.8+ and nothing else.
Run it with whichever of `python3`, `python` or `py -3` exists on this machine. Use the absolute path to the script.
Every call ends with one line `EF_TOOL_RESULT: {json}`; read that line. Run from the repository root.

Two modes. Ask which one the user wants unless it is obvious:
- **A. Scaffold, then split** (default): `dotnet ef dbcontext scaffold`, then split the configurations.
- **B. Split only**: the DbContext was already scaffolded; only move its configuration into separate classes.

## Mode A: scaffold, then split

1. **Discover.** `ef_tool.py discover --root <repo root>`. If `dotnetInstalled` is false, stop (the .NET SDK is
   required). If `efTool.installed` is false, ask, then run `dotnet tool install --global dotnet-ef`.
2. **Target project** (receives the generated files, `--project`): pick from `projects` (ignore test projects) or ask.
   **Startup project** (`--startup-project`): pick from `startupProjects` as in the other EF skills; ask if ambiguous.
3. **Packages.** Check that the target (or startup) `.csproj` references `Microsoft.EntityFrameworkCore.Design`
   and the provider package. If one is missing, tell the user and offer `dotnet add <csproj> package <name>` (ask first).
4. **Connection.** Prefer `Name=ConnectionStrings:DefaultConnection` (read from the startup project's configuration).
   If a full connection string is needed and it contains a password, do not ask the user to paste it into the chat.
   Ask them to put it in an environment variable before starting Claude Code (or use user-secrets) and pass
   `--connection-env <VAR>`. If the user pastes it anyway, use `--connection`; the displayed command masks passwords.
5. **Provider** (`--provider`): SQL Server `Microsoft.EntityFrameworkCore.SqlServer`, PostgreSQL
   `Npgsql.EntityFrameworkCore.PostgreSQL`, SQLite `Microsoft.EntityFrameworkCore.Sqlite`, MySQL
   `Pomelo.EntityFrameworkCore.MySql`, or any other package name the user gives.
6. **Options.** Ask only for what the user cares about: `--context` (class name; default `<Database>Context`),
   `--output-dir` (entities), `--context-dir`, `--force`, `--extra` (for example `"--table Documents --no-pluralize"`).
   Recommend `--no-onconfiguring`: without it EF writes the connection string into the DbContext source.
7. **Safety check.** If `--force` is used, run `git status --porcelain` (when the repo uses git). If there are
   uncommitted changes, warn that `--force` overwrites generated files and suggest committing first.
8. **Dry run.** `ef_tool.py ef scaffold --project <P> [--startup-project <S>] --connection-env <VAR> --provider <PKG> ...`
   without `--execute`. Show the command (passwords are masked) and ask for confirmation.
9. **Execute** with the same command plus `--execute`. The result lists `generatedContextFiles`.
   If it is empty or has several files, ask the user which one is the new DbContext.
   If the scaffold fails, open `references/troubleshooting.md` at "Scaffold fails".
10. Continue with **Split** below, using `--no-backup` (the file was just generated).

## Mode B: split only
Run `discover`, let the user choose the DbContext (its `file` is the context file), then continue with **Split**.
Use `--no-backup` when the file is tracked by git without uncommitted changes; otherwise keep the default `.bak` backup.

## Split
1. **How to apply the configurations in OnModelCreating.** Ask the user:
   - `--apply assembly` (industry standard): one `ApplyConfigurationsFromAssembly(typeof(<Context>).Assembly)` call;
     new configuration classes are picked up automatically. Do **not** use it when several DbContexts live in the same
     assembly (each context would apply every context's configurations); `otherContextsInSameProject > 0` in
     `discover` signals this.
   - `--apply each` (default of the script): one `ApplyConfiguration(new XConfiguration())` line per entity. Explicit and
     safe with several contexts in one assembly.
2. **Preview.** `ef_tool.py split-config --context-file <file> --apply <each|assembly> --dry-run`. Show what would be created.
3. **Run** without `--dry-run`. If it exits with code 3 (`needs-decision`), some `<Entity>Configuration.cs` files exist:
   list `alreadyExisting`, ask whether to `--on-existing overwrite` or `--on-existing keep`, then run again.
   With `keep` the entity block is still removed from `OnModelCreating`, so make sure the existing class is the one the
   user wants applied.
4. **Verify.** Run `dotnet build <project csproj>`. Report any compile errors and fix or explain them.

## Notes for the summary
- Blocks that are empty, share a class name with an earlier entity, or use unexpected syntax are **left untouched**
  in `OnModelCreating`; the result lists them (`skippedEmpty`, `skippedDuplicateClassName`, `unparsed`).
  For duplicates, tell the user to create that configuration by hand.
- "nothing-to-extract" means the context was already converted or the scaffold used data annotations only.
- Re-scaffolding with `--force` regenerates the context with all configuration inline again but leaves the
  `Configurations` folder alone; the split then asks about overwriting those files.
- Everything else in `OnModelCreating` (`HasSequence`, `HasDefaultSchema`, `OnModelCreatingPartial(...)`) stays where it is.

## Rules
- Never run `--execute` before the user has seen the command and confirmed it in this conversation.
- Never print passwords or paste full connection strings into your replies.
