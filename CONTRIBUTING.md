# Contributing

Thanks for taking the time. This is a small project, so the process is short.

## Getting set up

```bash
git clone https://github.com/Mario-Mohar/cloud-storage-deduplicator.git
cd cloud-storage-deduplicator
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

Python 3.11 is the floor. You do not need Google or Microsoft credentials to
work on this: every test runs against mocks, and the full suite finishes in
about a second.

## Running the checks

The pipeline runs exactly what you can run here:

```bash
ruff check .       # gating
pytest tests/ -v   # gating

black --check .    # reported, does not gate — see below
mypy drive_dedup   # reported, does not gate — see below
```

`ruff check .` uses the repository's own configuration, so what passes locally
is what passes in CI.

## Why two checks do not gate

`black` and `mypy` were declared in `pyproject.toml` long before anything ran
them. On the day the pipeline was added the code owed black 19 files and mypy
61 findings. Failing every unrelated pull request on that debt would only teach
people to ignore the pipeline, so both are reported on each pull request and
neither blocks. They should be promoted to gates once the debt is paid.

The lint set is the same story in miniature. `[tool.ruff]` used to name some
forty rule groups that had never been run — 1138 findings. What is enforced now
is the set the code actually passes, and `pyproject.toml` lists the rest as
direction of travel. Adding one group at a time and fixing what it finds is a
welcome kind of pull request.

## Working on this safely

**Nothing deletes.** The tool moves duplicates into a folder and records what
it moved so the move can be undone. Keep it that way: a change that deletes a
user's file, even one it is sure is a duplicate, is not in scope.

**Dry run is the default,** and it has to stay honest — a dry run that reports
something different from what the real run would do is worse than no dry run.

**Tests must not sleep.** One test used to sleep through a 70-second retry
backoff, which was 99% of the suite's runtime, and asserted only that the total
stayed under 100 seconds — an assertion a cap that was wrong by a factor of six
would still have passed. It now patches `time.sleep` and asserts the requested
delays. If you test retry or rate-limit behaviour, patch the clock and assert
the numbers.

## Pull requests

- Branch off `main`. Any branch name is fine.
- Commit messages follow `fix(scope):`, `feat(scope):`, `docs:`, `chore:`.
  The pipeline reads the pull request title's prefix to label it.
- The pipeline comments the result and updates that comment on every push.
  Green plus not-a-draft gets a `ready-to-merge` label.
- Maintainers can ask for a deeper look with `/claude review`.

A bug fix that comes with the test that would have caught it is the ideal, not
the entry fee.

## Reporting something

Use the issue templates. **Never paste an OAuth token, a client secret or a
`credentials.json`.** File names and folder ids are usually enough, and a
`--dry-run` transcript is the single most useful thing you can attach.

## Licence

MIT, same as the project. By contributing you agree your work ships under it.
