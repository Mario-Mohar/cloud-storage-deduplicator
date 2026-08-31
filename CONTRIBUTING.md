# Contributing

## Contributions are welcome

This is a small project maintained by one person in his spare time, and that is
exactly why an outside pair of eyes is worth a lot. **Finding a bug and writing
it down is a real contribution** — arguably the most useful one, because I only
ever use this on my own machine, with my own setup, and most of what is broken
is broken somewhere I never look.

Three ways to help, in the order of what they cost you:

### 1. Report something that is wrong

Open an issue with the **Bug report** template. It asks for what it does because
each field is something I would otherwise have to come back and ask for, which
costs us both a day.

What actually decides whether a report is useful:

- **What you expected, and what happened instead.** Both halves. "It does not
  work" is the one report I cannot act on.
- **The steps that get there.** If you can reproduce it, say how. If it only
  happened once, say that too — an intermittent bug is still worth knowing about,
  and "I could not reproduce it" is useful information rather than a
  disqualification.
- **Your setup**, as the template asks for it.

Do not polish it. A rough report today beats a perfect one that never gets
written. If in doubt whether something counts as a bug: open it. Deciding that
is my job, not yours.

### 2. Suggest something it should do

Open an issue with the **Feature request** template.

It asks what you are trying to *achieve* before what you want built, and that is
deliberate — not a hoop. Roughly half the time there turns out to be a simpler
answer than the one either of us had in mind, and it only surfaces if I know the
underlying situation.

A wish that gets declined is not a wasted issue. "Not now" and "not in this
project" are answers you will get quickly and with a reason.

### 3. Send a fix or a feature

Very welcome, and you do not need to ask permission for something small.

**For anything bigger than a few lines, open an issue first** — or comment on
the existing one — and say you are working on it. It costs you a sentence and
saves you the case where I fixed the same thing that evening, or where I would
have wanted it solved differently.

Because you cannot push to this repository, the route is through a fork:

```bash
# 1. Fork it on GitHub, then clone your fork
git clone https://github.com/<your-username>/cloud-storage-deduplicator.git
cd cloud-storage-deduplicator

# 2. A branch. Any name.
git switch -c fix/the-thing

# 3. Change what you came for, then run the checks below

# 4. Push to your fork and open the pull request
git push -u origin fix/the-thing
```

GitHub then offers you the pull request button. Fill in the template, and if it
closes an issue write `Fixes #12` so it closes itself on merge.

## What happens after you send it

1. **The pipeline runs** and posts a comment on your pull request with a table
   of what passed. It updates that same comment on every push, so there is one
   place to look rather than a growing pile.
2. **It labels the pull request** by size and type, and adds `ready-to-merge`
   once everything is green.
3. **On your very first contribution here, the checks wait for me to release
   them.** GitHub does that by default so that a stranger's code cannot use the
   runners unasked. If your pull request sits at "waiting for approval",
   **nothing is broken and you do not need to do anything** — I have to click
   once.
4. **I do the merging.** The default branch takes nothing that has not been
   through a pull request with green checks, and that holds for my own commits
   too.

If a check is red, the run log says which one and why. Ask in the pull request
if it is not obvious — a red pipeline is not a rejection, and quite often it is
the pipeline that is wrong rather than you.

I do this beside a job, so a reply can take a few days. It is not disinterest.

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

- Branch off `main` **in your fork** (see above). Any branch name is fine.
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
