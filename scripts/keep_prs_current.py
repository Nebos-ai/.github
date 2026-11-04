#!/usr/bin/env python3
"""Keep every open PR current with main the moment main changes its files (PHTW-212).

Why: a PR only conflicts when main changes a file the PR also changes. Nothing kept draft
PRs current (the nebos-frontend cascade skipped drafts, and red-pr-to-draft turns every red
PR into a draft), so drift grew unseen: on 2026-11-04, 20 of 102 open Nebos-ai PRs
conflicted, 22-722 commits behind main, 15 of them drafts. Merging main in as soon as main
touches one of the PR's files catches each conflict while it is one commit and one file.

What, per open same-repo PR targeting the base branch (drafts included):
  - up to date with main, or no file changed on main since the PR last took main in is
    also changed by the PR: nothing to do (a conflict needs a shared file);
  - in the merge queue: skipped (a queued branch cannot be pushed);
  - Dependabot's PR: never a bot commit (Dependabot stops rebasing a branch anyone else
    committed to) and never a command comment (Dependabot answers an App's "@dependabot
    rebase" with "only users with push access can use that command", 2026-11-04,
    nebos-backend #1845); a trial merge labels it "conflict" when it would conflict;
  - head commit is this bot's own and under DEBOUNCE_MINUTES old: skipped;
  - otherwise merge main in a throwaway worktree. Conflicts only in generated paths take
    main's copy and the caller's regen command rebuilds them; then the merge is committed
    as the bot and pushed with a plain push (never a force-push: a push that raced a
    newer commit is refused and the next main push retries). A conflict in any other file
    aborts, pushes nothing, labels the PR "conflict" and lists the files in the job
    summary; PHTW-213 routes the label to the PR's owner. Auto-merge PRs that merge
    cleanly are left alone, because the queue builds them on the latest main anyway.

Usage (from a checkout of the caller repo, origin fetched, GH_TOKEN set):
    python3 keep_prs_current.py [--dry-run] [--pr N]
Inputs come from flags or the KPC_* environment variables the reusable workflow sets.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Protocol

BOT_NAME = "nebbos-mcp-bot[bot]"
# GitHub's noreply address for the bot user of the Nebbos MCP bot App (id 5032125, user id
# 332450527); nebos-backend's scripts/check_commit_authors.py allow-lists exactly this.
BOT_EMAIL = "332450527+nebbos-mcp-bot[bot]@users.noreply.github.com"
BOT_ENV = {
    "GIT_AUTHOR_NAME": BOT_NAME,
    "GIT_AUTHOR_EMAIL": BOT_EMAIL,
    "GIT_COMMITTER_NAME": BOT_NAME,
    "GIT_COMMITTER_EMAIL": BOT_EMAIL,
}
DEBOUNCE_MINUTES = 30
CONFLICT_LABEL = "conflict"
CONFLICT_LABEL_COLOR = "B60205"
CONFLICT_LABEL_DESCRIPTION = "Merging main conflicts in a file that is not generated (keep-prs-current)"
DEPENDABOT_LOGINS = frozenset({"dependabot", "dependabot[bot]", "app/dependabot"})
SHA_TRAILER = "Main-SHA:"

PR_QUERY = """
query($owner: String!, $name: String!, $base: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, baseRefName: $base, first: 100, after: $after) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number headRefName headRefOid isDraft isCrossRepository isInMergeQueue
        author { login }
        autoMergeRequest { enabledAt }
        labels(first: 50) { nodes { name } }
      }
    }
  }
}
"""


@dataclass(frozen=True)
class Pr:
    number: int
    branch: str
    head_sha: str
    author: str = ""
    is_draft: bool = False
    is_fork: bool = False
    auto_merge: bool = False
    in_queue: bool = False
    labels: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Outcome:
    number: int
    result: str  # updated | conflict | would-update | skipped(<why>)
    files: tuple[str, ...] = ()

    def line(self) -> str:
        tail = f" [{', '.join(self.files)}]" if self.files else ""
        return f"PR #{self.number}: {self.result}{tail}"


class Github(Protocol):
    def add_label(self, number: int) -> None: ...

    def remove_label(self, number: int) -> None: ...



# ---------------------------------------------------------------- pure helpers (tested)


def parse_paths(text: str | None) -> tuple[str, ...]:
    """Split an input list on whitespace and commas; blanks dropped, order kept."""
    if not text:
        return ()
    return tuple(p for p in text.replace(",", " ").split() if p)


def is_generated(path: str, specs: Iterable[str]) -> bool:
    """A spec ending in '/' is a directory prefix, one with * ? [ a glob, else an exact path."""
    for spec in specs:
        if spec.endswith("/"):
            if path.startswith(spec):
                return True
        elif any(ch in spec for ch in "*?["):
            if fnmatch.fnmatchcase(path, spec):
                return True
        elif path == spec or path.startswith(spec + "/"):
            return True
    return False


def split_conflicts(files: Iterable[str], specs: Iterable[str]) -> tuple[list[str], list[str]]:
    """(generated, real) conflicted files, each sorted."""
    specs = tuple(specs)
    generated, real = [], []
    for f in sorted(set(files)):
        (generated if is_generated(f, specs) else real).append(f)
    return generated, real


def overlap(main_files: Iterable[str], pr_files: Iterable[str]) -> list[str]:
    return sorted(set(main_files) & set(pr_files))


def is_dependabot(login: str) -> bool:
    return login.lower() in DEPENDABOT_LOGINS


def debounced(
    author_email: str, commit_epoch: int, now_epoch: float, minutes: int = DEBOUNCE_MINUTES
) -> bool:
    """True when the branch tip is this bot's own commit from under ``minutes`` ago."""
    return author_email.lower() == BOT_EMAIL.lower() and now_epoch - commit_epoch < minutes * 60


def pr_from_node(node: dict) -> Pr:
    return Pr(
        number=int(node["number"]),
        branch=node["headRefName"],
        head_sha=node["headRefOid"],
        author=((node.get("author") or {}).get("login") or ""),
        is_draft=bool(node.get("isDraft")),
        is_fork=bool(node.get("isCrossRepository")),
        auto_merge=node.get("autoMergeRequest") is not None,
        in_queue=bool(node.get("isInMergeQueue")),
        labels=frozenset(n["name"] for n in ((node.get("labels") or {}).get("nodes") or [])),
    )


def summary_markdown(outcomes: list[Outcome], main_sha: str, dry_run: bool) -> str:
    head = f"### keep-prs-current{' (dry run)' if dry_run else ''}: main at `{main_sha[:12]}`\n\n"
    if not outcomes:
        return head + "No open PRs.\n"
    rows = ["| PR | Result | Files |", "|---|---|---|"]
    for o in outcomes:
        files = ", ".join(f"`{f}`" for f in o.files) or ""
        rows.append(f"| #{o.number} | {o.result} | {files} |")
    return head + "\n".join(rows) + "\n"


# ---------------------------------------------------------------- git


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)


def _bot_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """git with the bot identity in the environment, where it outranks any exported one."""
    return _git(cwd, *args, env={**os.environ, **BOT_ENV})


def _names(cwd: Path, *args: str) -> list[str]:
    out = _git(cwd, *args).stdout
    return [ln for ln in out.splitlines() if ln]


def _abort(wt: Path) -> None:
    _git(wt, "merge", "--abort")
    _git(wt, "reset", "-q", "--hard", "HEAD")


def take_main(wt: Path, files: Iterable[str], main_ref: str) -> bool:
    """Resolve each conflicted file to main's copy, or delete it when main deleted it."""
    for f in files:
        in_main = _git(wt, "cat-file", "-e", f"{main_ref}:{f}").returncode == 0
        if in_main:
            ok = _git(wt, "checkout", "-q", main_ref, "--", f).returncode == 0
            ok = ok and _git(wt, "add", "--", f).returncode == 0
        else:
            ok = _git(wt, "rm", "-q", "-f", "--", f).returncode == 0
        if not ok:
            return False
    return True


def link_shared_inputs(root: Path, wt: Path, paths: Iterable[str]) -> None:
    """Point the worktree at git-ignored inputs the job prepared in the main checkout.

    A missing target becomes a symlink to the whole directory (``frontend/node_modules``);
    an existing directory gets a symlink per missing file (``wheelhouse/`` keeps its
    committed wheels and gains the fetched ones). Never staged: only generated paths are.
    """
    for rel in paths:
        src, dst = root / rel, wt / rel
        if not src.exists():
            continue
        if not dst.exists() and not dst.is_symlink():
            if dst.parent.is_dir():
                dst.symlink_to(src, target_is_directory=src.is_dir())
        elif dst.is_dir() and src.is_dir():
            for child in src.iterdir():
                target = dst / child.name
                if not target.exists() and not target.is_symlink():
                    target.symlink_to(child, target_is_directory=child.is_dir())


def run_regen(wt: Path, command: str) -> bool:
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    proc = subprocess.run(["bash", "-c", command], cwd=wt, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip()[-600:]
        print(f"::warning::regen command failed in {wt.name}: {tail}")
        return False
    return True


def stage_generated(wt: Path, specs: Iterable[str]) -> bool:
    """Stage regenerated files under the generated specs only; git add refuses a spec
    matching nothing, so only specs present in the worktree or the index are passed."""
    present = [
        s for s in specs
        if any(ch in s for ch in "*?[") or (wt / s).exists()
        or _git(wt, "ls-files", "--", s).stdout.strip()
    ]  # fmt: skip
    return not present or _git(wt, "add", "-A", "--", *present).returncode == 0


# ---------------------------------------------------------------- one PR


@dataclass
class Settings:
    generated: tuple[str, ...] = ()
    regen_command: str = ""
    link_paths: tuple[str, ...] = ()
    dry_run: bool = False
    base: str = "main"


def process_pr(
    pr: Pr,
    *,
    gh: Github,
    root: Path,
    main_sha: str,
    settings: Settings,
    now: Callable[[], float] = time.time,
    regen: Callable[[Path, str], bool] = run_regen,
) -> Outcome:
    def out(result: str, files: Iterable[str] = ()) -> Outcome:
        return Outcome(pr.number, result, tuple(files))

    if pr.is_fork:
        return out("skipped(fork)")
    if pr.in_queue:
        return out("skipped(in merge queue)")
    remote_ref = f"refs/remotes/origin/{pr.branch}"
    if _git(root, "fetch", "-q", "origin", f"+refs/heads/{pr.branch}:{remote_ref}").returncode:
        return out("skipped(fetch failed)")
    tip = _git(root, "rev-parse", remote_ref).stdout.strip()
    if tip != pr.head_sha:
        return out("skipped(head moved since listing; next run)")
    base = _git(root, "merge-base", main_sha, tip).stdout.strip()
    if not base:
        return out("skipped(no merge base)")
    if base == main_sha:
        if CONFLICT_LABEL in pr.labels and not settings.dry_run:
            gh.remove_label(pr.number)
        return out("skipped(up to date)")
    shared = overlap(
        _names(root, "diff", "--name-only", "--no-renames", base, main_sha),
        _names(root, "diff", "--name-only", "--no-renames", base, tip),
    )
    if not shared:
        return out("skipped(no shared files)")

    stamp = _git(root, "log", "-1", "--format=%ct %ae", tip).stdout.split()
    commit_epoch, author_email = int(stamp[0]), (stamp[1] if len(stamp) > 1 else "")
    if not is_dependabot(pr.author) and debounced(author_email, commit_epoch, now()):
        return out(f"skipped(bot updated it under {DEBOUNCE_MINUTES} min ago)", shared)

    with tempfile.TemporaryDirectory(prefix="keep-prs-current-") as tmp:
        wt = Path(tmp) / "pr"
        if _git(root, "worktree", "add", "-q", "--detach", str(wt), tip).returncode:
            return out("skipped(worktree failed)")
        try:
            return _merge(pr, wt, root, main_sha, shared, settings, gh, regen, out)
        finally:
            _git(root, "worktree", "remove", "--force", str(wt))


def _merge(
    pr: Pr,
    wt: Path,
    root: Path,
    main_sha: str,
    shared: list[str],
    settings: Settings,
    gh: Github,
    regen: Callable[[Path, str], bool],
    out: Callable[..., Outcome],
) -> Outcome:
    merged = _bot_git(wt, "merge", "--no-commit", "--no-ff", main_sha)
    conflicted = _names(wt, "diff", "--name-only", "--diff-filter=U")
    if merged.returncode and not conflicted:
        _abort(wt)
        return out("skipped(merge failed)")
    if is_dependabot(pr.author):
        _abort(wt)
        if not conflicted:
            return out("skipped(dependabot rebases its own PRs)", shared)
        if not settings.dry_run and CONFLICT_LABEL not in pr.labels:
            gh.add_label(pr.number)
        return out("conflict", sorted(conflicted))
    generated, real = split_conflicts(conflicted, settings.generated)
    if real:
        _abort(wt)
        if not settings.dry_run and CONFLICT_LABEL not in pr.labels:
            gh.add_label(pr.number)
        return out("conflict", real)
    if pr.auto_merge and not generated:
        _abort(wt)
        return out("skipped(auto-merge; the queue builds on main)", shared)
    if settings.dry_run:
        _abort(wt)
        return out("would-update" + ("(regenerate)" if generated else ""), shared)
    if generated:
        if not settings.regen_command:
            _abort(wt)
            return out("skipped(generated conflict but no regen command)", generated)
        link_shared_inputs(root, wt, settings.link_paths)
        if not take_main(wt, generated, main_sha) or not regen(wt, settings.regen_command):
            _abort(wt)
            return out("skipped(regenerate failed)", generated)
        if not stage_generated(wt, settings.generated):
            _abort(wt)
            return out("skipped(stage failed)", generated)
    body = (
        f"{SHA_TRAILER} {main_sha}\n"
        f"Main changed files this PR also changes: {', '.join(shared)}."
        + (f"\nTook main's copy of {', '.join(generated)} and regenerated them." if generated else "")
        + "\n(PHTW-212; Nebos-ai/.github scripts/keep_prs_current.py)"
    )
    commit = _bot_git(wt, "commit", "-q", "-m", f"chore: merge {settings.base} into {pr.branch} [bot]", "-m", body)
    if commit.returncode:
        _abort(wt)
        return out("skipped(commit failed)")
    if _git(wt, "push", "-q", "origin", f"HEAD:refs/heads/{pr.branch}").returncode:
        return out("skipped(push refused; a newer commit landed first)")
    if CONFLICT_LABEL in pr.labels:
        gh.remove_label(pr.number)
    return out("updated" + ("(regenerated)" if generated else ""), shared)


# ---------------------------------------------------------------- GitHub (gh CLI)


class GhCli:
    def __init__(self, repo: str) -> None:
        self.repo = repo
        self._label_ready = False

    def _gh(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True)
        if check and proc.returncode:
            raise RuntimeError(f"gh {' '.join(args[:2])} failed: {proc.stderr.strip()[-400:]}")
        return proc.stdout

    def list_prs(self, base: str) -> list[Pr]:
        owner, name = self.repo.split("/", 1)
        prs: list[Pr] = []
        after: str | None = None
        while True:
            args = ["api", "graphql", "-f", f"query={PR_QUERY}", "-F", f"owner={owner}",
                    "-F", f"name={name}", "-F", f"base={base}"]  # fmt: skip
            if after:
                args += ["-F", f"after={after}"]
            page = json.loads(self._gh(*args))["data"]["repository"]["pullRequests"]
            prs += [pr_from_node(n) for n in page["nodes"]]
            if not page["pageInfo"]["hasNextPage"]:
                return sorted(prs, key=lambda p: p.number)
            after = page["pageInfo"]["endCursor"]

    def _ensure_label(self) -> None:
        if self._label_ready:
            return
        self._gh("api", f"repos/{self.repo}/labels", "-f", f"name={CONFLICT_LABEL}",
                 "-f", f"color={CONFLICT_LABEL_COLOR}", "-f",
                 f"description={CONFLICT_LABEL_DESCRIPTION}", check=False)  # fmt: skip
        self._label_ready = True  # 422 means it already exists

    def add_label(self, number: int) -> None:
        self._ensure_label()
        self._gh("api", f"repos/{self.repo}/issues/{number}/labels", "-f", f"labels[]={CONFLICT_LABEL}")

    def remove_label(self, number: int) -> None:
        self._gh("api", "-X", "DELETE", f"repos/{self.repo}/issues/{number}/labels/{CONFLICT_LABEL}", check=False)



# ---------------------------------------------------------------- main


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", default=_env_bool("KPC_DRY_RUN"))
    ap.add_argument("--pr", type=int, help="process only this PR number")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--base", default=os.environ.get("KPC_BASE") or "main")
    ap.add_argument("--generated-paths", default=os.environ.get("KPC_GENERATED_PATHS", ""))
    ap.add_argument("--regen-command", default=os.environ.get("KPC_REGEN_COMMAND", ""))
    ap.add_argument("--link-paths", default=os.environ.get("KPC_LINK_PATHS", ""))
    args = ap.parse_args(argv)
    if not args.repo:
        ap.error("--repo or GITHUB_REPOSITORY is required")

    root = Path.cwd()
    settings = Settings(
        generated=parse_paths(args.generated_paths),
        regen_command=args.regen_command.strip(),
        link_paths=parse_paths(args.link_paths),
        dry_run=args.dry_run,
        base=args.base,
    )
    fetched = _git(root, "fetch", "-q", "origin", f"+refs/heads/{args.base}:refs/remotes/origin/{args.base}")
    if fetched.returncode:
        print(f"::error::cannot fetch {args.base}: {fetched.stderr.strip()}")
        return 1
    main_sha = _git(root, "rev-parse", f"origin/{args.base}").stdout.strip()
    gh = GhCli(args.repo)
    prs = [p for p in gh.list_prs(args.base) if args.pr is None or p.number == args.pr]
    print(f"{args.base} = {main_sha}; open PRs: {[p.number for p in prs] or 'none'}")
    outcomes: list[Outcome] = []
    for pr in prs:
        try:
            outcome = process_pr(pr, gh=gh, root=root, main_sha=main_sha, settings=settings)
        except Exception as exc:  # one PR never stops the others
            outcome = Outcome(pr.number, f"skipped(error: {type(exc).__name__}: {exc})")
        print(outcome.line())
        outcomes.append(outcome)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(summary_markdown(outcomes, main_sha, settings.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
