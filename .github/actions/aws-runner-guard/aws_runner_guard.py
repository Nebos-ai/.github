#!/usr/bin/env python3
"""aws-runner-guard: keep secrets off CI jobs that can be routed to AWS runners.

Tracker task AUT-113 (parent AUT-104); exit criterion of governance ADR #2997 (AD-6, AD-13 item 2).

What it does, for the repository it is run in (read every workflow file with a YAML parser):

1. BLOCKING. A job is "variable-routed" when its runner can come from an org variable
   NEBBOS_CI_RUNNER_* (runs-on reads it directly, through a matrix, through a reusable-workflow
   input, through a needed job's output, or a reusable-workflow call passes it). Such a job runs
   on shared, ephemeral AWS machines, so it must not reference any secret that is not listed in
   the repo's allowlist. GITHUB_TOKEN is always allowed. Workflow-level `env:` that references a
   secret counts for every job in the file. `secrets: inherit`, a bare `secrets` context
   (toJSON(secrets)) and a job `environment:` (it carries environment secrets) count as findings.
2. PENDING FIXES. Jobs that exist today with an offending secret are listed in `pending_fixes`
   of the allowlist file, each naming the task that removes the secret. They are reported, not
   failed, but only for the secrets listed, and only while the runner variables still point at
   GitHub-hosted runners. When a runner variable is flipped to a non-hosted label, any variable-
   routed job still in `pending_fixes` fails the check: it is refused as a flip target.
3. WARN-FIRST. Jobs named in `heavy_jobs` (the list from research record 17ed3f20) that still
   hard-code `runs-on: ubuntu-*` produce a warning, never a failure.

Names only are printed; no secret value is ever read (this script never sees one).
Exit status: 0 pass, 1 findings, 2 setup error (missing or invalid allowlist, YAML that does not parse).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

ROUTE_RE = re.compile(r"""\bvars\s*(?:\.\s*|\[\s*['"])NEBBOS_CI_RUNNER_[A-Za-z0-9_]*""", re.IGNORECASE)
EXPR_RE = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
SECRET_DOT_RE = re.compile(r"(?<![\w.])secrets\s*\.\s*([A-Za-z_][\w-]*)", re.IGNORECASE)
SECRET_BRACKET_RE = re.compile(r"""(?<![\w.])secrets\s*\[\s*['"]([^'"]+)['"]\s*\]""", re.IGNORECASE)
STRING_LITERAL_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
BARE_SECRETS_RE = re.compile(r"(?<![\w.])secrets(?![\w])", re.IGNORECASE)
INPUT_RE = re.compile(r"\binputs\.([\w-]+)")
NEEDS_OUT_RE = re.compile(r"\bneeds\.([\w-]+)\.outputs\.([\w-]+)")
UBUNTU_RE = re.compile(r"\bubuntu-[\w.]+", re.IGNORECASE)
TASK_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
HOSTED_PREFIXES = ("ubuntu-", "windows-", "macos-")
IMPLICIT_ALLOWED = {"GITHUB_TOKEN"}
ALL_SECRETS = "*(whole secrets context)"
INHERIT = "*(secrets: inherit)"
DEFAULT_ALLOWLIST = ".github/aws-runner-secrets-allowlist.yml"


class SetupError(Exception):
    """The guard cannot run: allowlist missing or invalid, or a workflow does not parse."""


# ---------------------------------------------------------------- YAML node helpers


def mget(node: Node | None, key: str) -> Node | None:
    if not isinstance(node, MappingNode):
        return None
    for k, v in node.value:
        if isinstance(k, ScalarNode) and k.value == key:
            return v
    return None


def sval(node: Node | None) -> str | None:
    return node.value if isinstance(node, ScalarNode) else None


def iter_scalars(node: Node | None, _seen: set[int] | None = None) -> Iterator[tuple[str, int, bool]]:
    """Yield (text, line, is_raw_expression) for every scalar value under node.

    A value under an `if` key is a raw expression (no ${{ }} needed). Comments are never nodes,
    so a commented-out secret is not a reference.
    """
    if node is None:
        return
    seen = _seen if _seen is not None else set()
    if id(node) in seen:
        return
    seen.add(id(node))
    if isinstance(node, ScalarNode):
        yield node.value, node.start_mark.line + 1, False
    elif isinstance(node, SequenceNode):
        for item in node.value:
            yield from iter_scalars(item, seen)
    elif isinstance(node, MappingNode):
        for k, v in node.value:
            if isinstance(k, ScalarNode) and k.value == "if" and isinstance(v, ScalarNode):
                yield v.value, v.start_mark.line + 1, True
            else:
                yield from iter_scalars(v, seen)


def flat(node: Node | None) -> str:
    return "\n".join(t for t, _, _ in iter_scalars(node))


def secret_refs(text: str, raw_expression: bool) -> list[str]:
    """Secret names referenced in one scalar. The whole-context case yields ALL_SECRETS."""
    bodies = EXPR_RE.findall(text)
    if raw_expression and not bodies:
        bodies = [text]
    names: list[str] = []
    for body in bodies:
        for m in SECRET_DOT_RE.finditer(body):
            names.append(m.group(1))
        for m in SECRET_BRACKET_RE.finditer(body):
            names.append(m.group(1))
        rest = SECRET_DOT_RE.sub(" ", body)
        rest = SECRET_BRACKET_RE.sub(" ", rest)
        rest = STRING_LITERAL_RE.sub(" ", rest)
        if BARE_SECRETS_RE.search(rest):
            names.append(ALL_SECRETS)
    return names


def normalise(name: str) -> str:
    return name if name.startswith("*") else name.upper()


# ---------------------------------------------------------------- workflow model


@dataclass
class Job:
    workflow: str
    job_id: str
    node: MappingNode
    line: int
    routed: bool = False
    route_reason: str = ""
    hard_ubuntu: bool = False
    secrets: dict[str, list[int]] = field(default_factory=dict)


@dataclass
class Workflow:
    path: str
    root: MappingNode
    jobs: dict[str, Job] = field(default_factory=dict)


def load_workflows(root: Path, workflows_dir: str) -> list[Workflow]:
    wf_dir = root / workflows_dir
    result: list[Workflow] = []
    if not wf_dir.is_dir():
        return result
    for p in sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))):
        rel = p.relative_to(root).as_posix()
        try:
            node = yaml.compose(p.read_text(encoding="utf-8"), Loader=yaml.SafeLoader)
        except yaml.YAMLError as exc:
            raise SetupError(f"{rel}: YAML does not parse: {str(exc).splitlines()[0]}") from exc
        if node is None:
            continue
        if not isinstance(node, MappingNode):
            raise SetupError(f"{rel}: top level is not a mapping")
        wf = Workflow(path=rel, root=node)
        jobs_node = mget(node, "jobs")
        if isinstance(jobs_node, MappingNode):
            for k, v in jobs_node.value:
                if isinstance(k, ScalarNode) and isinstance(v, MappingNode):
                    wf.jobs[k.value] = Job(workflow=rel, job_id=k.value, node=v, line=k.start_mark.line + 1)
        result.append(wf)
    return result


def local_callers(workflows: list[Workflow]) -> dict[str, list[MappingNode | None]]:
    """Map a local reusable workflow's path to the `with:` node of each job that calls it."""
    callers: dict[str, list[MappingNode | None]] = {}
    for wf in workflows:
        for job in wf.jobs.values():
            uses = sval(mget(job.node, "uses"))
            if uses and uses.startswith("./"):
                target = uses[2:].split("@", 1)[0]
                with_node = mget(job.node, "with")
                callers.setdefault(target, []).append(with_node if isinstance(with_node, MappingNode) else None)
    return callers


def classify(workflows: list[Workflow]) -> None:
    callers = local_callers(workflows)
    for wf in workflows:
        env_scalars = list(iter_scalars(mget(wf.root, "env")))
        for job in wf.jobs.values():
            runs_on = mget(job.node, "runs-on")
            ro_text = flat(runs_on)
            reason = ""
            if ROUTE_RE.search(ro_text):
                reason = "runs-on reads a NEBBOS_CI_RUNNER variable"
            elif sval(mget(job.node, "uses")) and ROUTE_RE.search(flat(mget(job.node, "with"))):
                reason = "reusable-workflow call passes a NEBBOS_CI_RUNNER variable"
            elif "matrix." in ro_text and ROUTE_RE.search(flat(mget(job.node, "strategy"))):
                reason = "runs-on comes from a matrix built from a NEBBOS_CI_RUNNER variable"
            if not reason:
                for m in INPUT_RE.finditer(ro_text):
                    for with_node in callers.get(wf.path, []):
                        if ROUTE_RE.search(flat(mget(with_node, m.group(1)))):
                            reason = f"runs-on reads input {m.group(1)}, which a caller sets from a NEBBOS_CI_RUNNER variable"
                            break
                    if reason:
                        break
            if not reason:
                for m in NEEDS_OUT_RE.finditer(ro_text):
                    other = wf.jobs.get(m.group(1))
                    if other is not None and ROUTE_RE.search(flat(other.node)):
                        reason = f"runs-on reads an output of job {m.group(1)}, which reads a NEBBOS_CI_RUNNER variable"
                        break
            job.routed = bool(reason)
            job.route_reason = reason
            job.hard_ubuntu = (
                not reason
                and not sval(mget(job.node, "uses"))
                and "${{" not in ro_text
                and bool(UBUNTU_RE.search(ro_text))
            )
            collect_secrets(job, env_scalars)
            if job.routed:
                add_exposure_findings(job)


def collect_secrets(job: Job, env_scalars: list[tuple[str, int, bool]]) -> None:
    for text, line, raw in list(iter_scalars(job.node)) + env_scalars:
        for name in secret_refs(text, raw):
            n = normalise(name)
            if n not in IMPLICIT_ALLOWED:
                job.secrets.setdefault(n, []).append(line)


def add_exposure_findings(job: Job) -> None:
    sec = mget(job.node, "secrets")
    if sval(sec) == "inherit":
        job.secrets.setdefault(INHERIT, []).append(sec.start_mark.line + 1 if sec else job.line)
    env = mget(job.node, "environment")
    env_name = sval(env) if isinstance(env, ScalarNode) else sval(mget(env, "name"))
    if env_name:
        job.secrets.setdefault(f"ENVIRONMENT:{env_name.upper()}", []).append(env.start_mark.line + 1 if env else job.line)


# ---------------------------------------------------------------- allowlist


@dataclass
class Pending:
    workflow: str
    job: str
    secrets: set[str]
    task: str


@dataclass
class Allowlist:
    allowed: dict[str, str]
    pending: dict[tuple[str, str], Pending]
    heavy: list[tuple[str, str]]


def load_allowlist(path: Path) -> Allowlist:
    if not path.is_file():
        raise SetupError(f"{path.as_posix()}: allowlist file is missing (an empty one still needs the file)")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise SetupError(f"{path.as_posix()}: YAML does not parse: {str(exc).splitlines()[0]}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise SetupError(f"{path.as_posix()}: top level must be a mapping with allowed, pending_fixes, heavy_jobs")
    unknown = set(data) - {"allowed", "pending_fixes", "heavy_jobs"}
    if unknown:
        raise SetupError(f"{path.as_posix()}: unknown keys {sorted(unknown)}")

    allowed_raw = data.get("allowed") or {}
    if not isinstance(allowed_raw, dict):
        raise SetupError("allowed must be a mapping of secret name to a one-line reason")
    allowed: dict[str, str] = {}
    for name, reason in allowed_raw.items():
        if not isinstance(name, str) or not isinstance(reason, str) or not reason.strip() or "\n" in reason.strip():
            raise SetupError(f"allowed.{name}: needs a one-line reason string")
        allowed[normalise(name)] = reason.strip()

    pending: dict[tuple[str, str], Pending] = {}
    for i, entry in enumerate(data.get("pending_fixes") or []):
        if not isinstance(entry, dict):
            raise SetupError(f"pending_fixes[{i}]: must be a mapping")
        wf, job, secrets, task = entry.get("workflow"), entry.get("job"), entry.get("secrets"), entry.get("task")
        if not (isinstance(wf, str) and isinstance(job, str)):
            raise SetupError(f"pending_fixes[{i}]: workflow and job are required strings")
        if not (isinstance(secrets, list) and secrets and all(isinstance(s, str) for s in secrets)):
            raise SetupError(f"pending_fixes[{i}]: secrets must be a non-empty list of names")
        if not (isinstance(task, str) and TASK_RE.match(task)):
            raise SetupError(f"pending_fixes[{i}]: task must be a tracker key such as AUT-111")
        pending[(wf, job)] = Pending(wf, job, {normalise(s) for s in secrets}, task)

    heavy: list[tuple[str, str]] = []
    for i, entry in enumerate(data.get("heavy_jobs") or []):
        if not (isinstance(entry, dict) and isinstance(entry.get("workflow"), str) and isinstance(entry.get("job"), str)):
            raise SetupError(f"heavy_jobs[{i}]: workflow and job are required strings")
        heavy.append((entry["workflow"], entry["job"]))
    return Allowlist(allowed, pending, heavy)


# ---------------------------------------------------------------- evaluation


def runner_is_hosted(value: str) -> bool:
    """True when an org runner variable still names GitHub-hosted runners (or is unset)."""
    v = (value or "").strip()
    if not v:
        return True
    try:
        parsed = json.loads(v)
    except ValueError:
        parsed = v
    labels = parsed if isinstance(parsed, list) else [parsed]
    return bool(labels) and all(isinstance(x, str) and x.lower().startswith(HOSTED_PREFIXES) for x in labels)


@dataclass
class Finding:
    level: str  # error | warning | notice
    file: str
    line: int
    message: str


def evaluate(workflows: list[Workflow], allow: Allowlist, runner_values: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    flipped = {k: v for k, v in runner_values.items() if not runner_is_hosted(v)}
    index = {(wf.path, j.job_id): j for wf in workflows for j in wf.jobs.values()}

    for job in index.values():
        if not job.routed:
            continue
        offending = {n: lines for n, lines in job.secrets.items() if n not in allow.allowed}
        entry = allow.pending.get((job.workflow, job.job_id))
        where = f"{job.workflow}:{job.job_id}"
        if entry is None:
            for name, lines in sorted(offending.items()):
                findings.append(Finding(
                    "error", job.workflow, lines[0],
                    f"{where} can run on AWS runners ({job.route_reason}) and references secret {name}, "
                    f"which is not in the allowlist. Remove the reference or add it to `allowed` with a reason.",
                ))
            continue
        extra = {n: lines for n, lines in offending.items() if n not in entry.secrets}
        for name, lines in sorted(extra.items()):
            findings.append(Finding(
                "error", job.workflow, lines[0],
                f"{where} is in pending_fixes ({entry.task}) for other secrets, and now also references {name}. "
                f"Pending entries cover only the listed secrets.",
            ))
        for name in sorted(set(offending) & entry.secrets):
            findings.append(Finding(
                "warning", job.workflow, offending[name][0],
                f"{where} references {name} while variable-routed; tolerated until {entry.task} removes it.",
            ))
        if flipped:
            findings.append(Finding(
                "error", job.workflow, job.line,
                f"{where} is in pending_fixes ({entry.task}) and is variable-routed, but "
                f"{', '.join(sorted(flipped))} no longer names a GitHub-hosted runner. Refused as a flip target: "
                f"finish {entry.task} (remove the secret and the pending entry) before pointing the variable at AWS.",
            ))

    for (wf_path, job_id), entry in sorted(allow.pending.items()):
        job = index.get((wf_path, job_id))
        if job is None:
            findings.append(Finding("warning", wf_path, 1, f"pending_fixes entry {wf_path}:{job_id} ({entry.task}) names a job that no longer exists; remove it."))
            continue
        live = (set(job.secrets) & entry.secrets) - set(allow.allowed)
        if not live:
            findings.append(Finding("warning", wf_path, job.line, f"pending_fixes entry {wf_path}:{job_id} ({entry.task}) no longer matches any secret reference; remove it."))
        elif not job.routed:
            findings.append(Finding("notice", wf_path, job.line, f"{wf_path}:{job_id} ({entry.task}) holds {', '.join(sorted(live))} and is not variable-routed right now; it would be refused if it became so."))

    for wf_path, job_id in allow.heavy:
        job = index.get((wf_path, job_id))
        if job is None:
            findings.append(Finding("warning", wf_path, 1, f"heavy_jobs entry {wf_path}:{job_id} names a job that no longer exists; remove it."))
        elif job.hard_ubuntu:
            findings.append(Finding(
                "warning", wf_path, job.line,
                f"{wf_path}:{job_id} is a heavy job (research record 17ed3f20) and hard-codes runs-on: ubuntu-*; "
                f"read the NEBBOS_CI_RUNNER variables instead.",
            ))
    return findings


# ---------------------------------------------------------------- output


def emit(findings: list[Finding], workflows: list[Workflow], routed_count: int) -> None:
    gha = os.environ.get("GITHUB_ACTIONS") == "true"
    for f in findings:
        if gha:
            print(f"::{f.level} file={f.file},line={f.line}::{f.message}")
        else:
            print(f"{f.level.upper()} {f.file}:{f.line}: {f.message}")
    errors = sum(1 for f in findings if f.level == "error")
    warnings = sum(1 for f in findings if f.level == "warning")
    jobs = sum(len(w.jobs) for w in workflows)
    print(f"AWS_RUNNER_GUARD workflows={len(workflows)} jobs={jobs} variable_routed={routed_count} errors={errors} warnings={warnings}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = ["### AWS runner guard", "", f"{len(workflows)} workflows, {jobs} jobs, {routed_count} variable-routed. Errors: {errors}. Warnings: {warnings}.", ""]
        if findings:
            lines += ["| Level | Where | Finding |", "|---|---|---|"]
            lines += [f"| {f.level} | `{f.file}:{f.line}` | {f.message.replace('|', '/')} |" for f in findings]
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--workflows-dir", default=".github/workflows")
    ap.add_argument("--allowlist", default=DEFAULT_ALLOWLIST)
    ap.add_argument("--runner-pr", default=os.environ.get("NEBBOS_CI_RUNNER_PR", ""), help="value of org variable NEBBOS_CI_RUNNER_PR")
    ap.add_argument("--runner-queue", default=os.environ.get("NEBBOS_CI_RUNNER_QUEUE", ""), help="value of org variable NEBBOS_CI_RUNNER_QUEUE")
    args = ap.parse_args(argv)
    root = Path(args.repo_root)
    try:
        allow = load_allowlist(root / args.allowlist)
        workflows = load_workflows(root, args.workflows_dir)
    except SetupError as exc:
        print(f"::error::AWS runner guard setup: {exc}" if os.environ.get("GITHUB_ACTIONS") == "true" else f"SETUP ERROR: {exc}")
        return 2
    classify(workflows)
    findings = evaluate(workflows, allow, {"NEBBOS_CI_RUNNER_PR": args.runner_pr, "NEBBOS_CI_RUNNER_QUEUE": args.runner_queue})
    routed = sum(1 for w in workflows for j in w.jobs.values() if j.routed)
    emit(findings, workflows, routed)
    return 1 if any(f.level == "error" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
