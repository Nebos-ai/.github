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
3. LITERAL AWS LABELS (AUT-120). A job whose runs-on names a literal `nebbos-ci-*` label (string, list,
   mapping labels, or text inside a `${{ }}` expression) is routed too: every check above applies. A job
   that can reach the trusted `nebbos-ci-ondemand` pool on an untrusted event is an error (ADR AD-7a: the
   On-Demand pool may write the `trusted/` cache prefix; it is for merge_group and main only). Trusted
   events are `merge_group` and `push` limited to `branches: [main]`; any other event in the workflow's
   `on:` (pull_request, pull_request_target, workflow_dispatch, schedule, push without a main-only
   branch filter, workflow_call, ...) is untrusted. A literal ondemand label inside an expression is an
   error: use the variable routing (`vars.NEBBOS_CI_RUNNER_QUEUE`) instead.
4. WARN-FIRST. Jobs named in `heavy_jobs` (the list from research record 17ed3f20) that still
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
AWS_LABEL_RE = re.compile(r"(?<![\w.-])nebbos-ci-[\w.-]+", re.IGNORECASE)
ONDEMAND_PREFIX = "nebbos-ci-ondemand"
TRUSTED_EVENTS = {"merge_group"}
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
class Reach:
    """One literal nebbos-ci-* label that a job can run on, and the untrusted events that reach it."""

    label: str
    file: str
    line: int
    bad_events: list[str]
    in_expression: bool = False


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
    reaches: list[Reach] = field(default_factory=list)
    unresolved: list[tuple[int, str]] = field(default_factory=list)  # (line, why)


@dataclass
class Workflow:
    path: str
    root: MappingNode
    bad_events: list[str] = field(default_factory=list)
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
        wf = Workflow(path=rel, root=node, bad_events=untrusted_events(node))
        jobs_node = mget(node, "jobs")
        if isinstance(jobs_node, MappingNode):
            for k, v in jobs_node.value:
                if isinstance(k, ScalarNode) and isinstance(v, MappingNode):
                    wf.jobs[k.value] = Job(workflow=rel, job_id=k.value, node=v, line=k.start_mark.line + 1)
        result.append(wf)
    return result


def untrusted_events(root: MappingNode) -> list[str]:
    """Events in `on:` that are not merge_group or a push limited to branches: [main]. No `on:` at all
    counts as one untrusted entry, because the guard cannot show the job is unreachable from a PR."""
    on = mget(root, "on")
    if on is None:
        return ["(no on: key)"]
    bad: list[str] = []
    if isinstance(on, ScalarNode):
        names = [(on.value, None)]
    elif isinstance(on, SequenceNode):
        names = [(sval(i) or "?", None) for i in on.value]
    elif isinstance(on, MappingNode):
        names = [(k.value, v) for k, v in on.value if isinstance(k, ScalarNode)]
    else:
        return ["(unreadable on:)"]
    for name, cfg in names:
        if name in TRUSTED_EVENTS:
            continue
        if name == "push" and isinstance(cfg, MappingNode) and push_is_main_only(cfg):
            continue
        bad.append(name)
    return bad


def push_is_main_only(cfg: MappingNode) -> bool:
    if mget(cfg, "tags") is not None or mget(cfg, "branches-ignore") is not None:
        return False
    br = mget(cfg, "branches")
    if isinstance(br, ScalarNode):
        return br.value == "main"
    if isinstance(br, SequenceNode):
        return bool(br.value) and all(sval(i) == "main" for i in br.value)
    return False


VARS_ROUTE_FULL_RE = re.compile(
    r"""\bvars\s*(?:\.\s*NEBBOS_CI_RUNNER_[A-Za-z0-9_]*|\[\s*['"]NEBBOS_CI_RUNNER_[A-Za-z0-9_]*['"]\s*\])""", re.IGNORECASE
)
CHAIN_RE = re.compile(r"[A-Za-z_][\w-]*(?:\s*\.\s*[\w-]+)*")
# Names a runs-on expression may use besides the sanctioned routing; nothing here is set by a PR author.
SAFE_CHAINS = {"fromjson", "tojson", "github.event_name", "github.ref", "github.ref_name", "true", "false", "null"}
UNRESOLVED_MSG = (
    "runs-on expression the guard cannot resolve; use a literal label, a matrix of literals "
    "or vars.NEBBOS_CI_RUNNER_* routing"
)


def local_callers(workflows: list[Workflow]) -> dict[str, list[tuple[Workflow, Job, MappingNode | None]]]:
    """Map a local reusable workflow's path to (caller workflow, caller job, `with:` node) per caller."""
    callers: dict[str, list[tuple[Workflow, Job, MappingNode | None]]] = {}
    for wf in workflows:
        for job in wf.jobs.values():
            uses = sval(mget(job.node, "uses"))
            if uses and uses.startswith("./"):
                target = uses[2:].split("@", 1)[0]
                with_node = mget(job.node, "with")
                callers.setdefault(target, []).append((wf, job, with_node if isinstance(with_node, MappingNode) else None))
    return callers


class Analysis:
    """What one job's runner can be, built up while reading its runs-on."""

    def __init__(self, wf: Workflow, job: Job):
        self.wf, self.job = wf, job
        self.reasons: list[str] = []

    def route(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)

    def label(self, label: str, file: str, line: int, bad: list[str], expr: bool = False) -> None:
        self.route("runs-on names a literal nebbos-ci-* AWS runner label")
        if label.lower().startswith(ONDEMAND_PREFIX):
            self.job.reaches.append(Reach(label, file, line, bad, expr))

    def unresolved(self, line: int, why: str = UNRESOLVED_MSG) -> None:
        self.job.unresolved.append((line, why))


def judge_text(an: Analysis, text: str, line: int, file: str, bad: list[str]) -> bool:
    """Judge one plain scalar value (a label, or a value passed through matrix/with). Returns False when
    the value is an expression the guard cannot resolve."""
    ok = True
    outside = EXPR_RE.sub(" ", text)
    for m in AWS_LABEL_RE.finditer(outside):
        an.label(m.group(0), file, line, bad)
    for body in EXPR_RE.findall(text):
        if ROUTE_RE.search(body):
            an.route("runs-on reads a NEBBOS_CI_RUNNER variable")
        found = False
        for m in AWS_LABEL_RE.finditer(body):
            an.label(m.group(0), file, line, bad, expr=True)
            found = True
        if not found and not ROUTE_RE.search(body):
            ok = False
    return ok


def analyse_runs_on_expr(an: Analysis, wf: Workflow, job: Job, body: str, line: int, callers: dict) -> None:
    sanctioned = bool(ROUTE_RE.search(body))
    s2 = VARS_ROUTE_FULL_RE.sub(" ", body)
    if sanctioned:
        an.route("runs-on reads a NEBBOS_CI_RUNNER variable")
    for lit in STRING_LITERAL_RE.findall(s2):
        inner = lit[1:-1]
        hits = list(AWS_LABEL_RE.finditer(inner))
        for m in hits:
            an.label(m.group(0), wf.path, line, wf.bad_events, expr=True)
        if not hits and "nebbos" in inner.lower():
            an.unresolved(line, "runs-on builds a nebbos-ci label from pieces; use a literal label or vars.NEBBOS_CI_RUNNER_* routing")
    for raw in CHAIN_RE.findall(STRING_LITERAL_RE.sub(" ", s2)):
        chain = re.sub(r"\s+", "", raw).lower()
        if chain in SAFE_CHAINS:
            continue
        if chain.startswith("matrix.") and chain.count(".") == 1:
            resolve_matrix(an, wf, job, chain.split(".", 1)[1], line)
        elif chain.startswith("inputs.") and chain.count(".") == 1:
            resolve_input(an, wf, chain.split(".", 1)[1], line, callers)
        else:
            m = NEEDS_OUT_RE.fullmatch(raw.replace(" ", ""))
            other = wf.jobs.get(m.group(1)) if m else None
            if other is not None and ROUTE_RE.search(flat(other.node)):
                an.route(f"runs-on reads an output of job {m.group(1)}, which reads a NEBBOS_CI_RUNNER variable")
            else:
                an.unresolved(line)


def resolve_matrix(an: Analysis, wf: Workflow, job: Job, key: str, line: int) -> None:
    strategy = mget(job.node, "strategy")
    matrix = mget(strategy, "matrix")
    if ROUTE_RE.search(flat(strategy)):
        an.route("runs-on comes from a matrix built from a NEBBOS_CI_RUNNER variable")
    if not isinstance(matrix, MappingNode):
        if not ROUTE_RE.search(flat(strategy)):
            an.unresolved(line)
        return
    values: list[Node] = []
    top = mget(matrix, key)
    if isinstance(top, SequenceNode):
        values += list(top.value)
    elif top is not None:
        values.append(top)
    inc = mget(matrix, "include")
    if isinstance(inc, SequenceNode):
        for entry in inc.value:
            v = mget(entry, key)
            if v is not None:
                values += list(v.value) if isinstance(v, SequenceNode) else [v]
    elif inc is not None:
        an.unresolved(line)
    if not values:
        an.unresolved(line)
    for v in values:
        if not isinstance(v, ScalarNode):
            an.unresolved(line)
        elif not judge_text(an, v.value, v.start_mark.line + 1, wf.path, wf.bad_events) and not ROUTE_RE.search(flat(strategy)):
            an.unresolved(line)


def resolve_input(an: Analysis, wf: Workflow, key: str, line: int, callers: dict) -> None:
    on = mget(wf.root, "on")
    if not (isinstance(on, MappingNode) and mget(on, "workflow_call") is not None or sval(on) == "workflow_call"
            or (isinstance(on, SequenceNode) and any(sval(i) == "workflow_call" for i in on.value))):
        an.unresolved(line)  # an input set by whoever dispatches the workflow
        return
    if isinstance(on, MappingNode) and mget(on, "workflow_dispatch") is not None:
        an.unresolved(line)
        return
    for cwf, cjob, with_node in callers.get(wf.path, []):
        v = mget(with_node, key)
        if v is None:
            default = mget(mget(mget(mget(on, "workflow_call"), "inputs"), key), "default") if isinstance(on, MappingNode) else None
            if default is None:
                an.unresolved(line)
                continue
            v = default
        if not isinstance(v, ScalarNode):
            an.unresolved(line)
        elif not judge_text(an, v.value, v.start_mark.line + 1, cwf.path, cwf.bad_events):
            an.unresolved(line)


def classify(workflows: list[Workflow]) -> None:
    callers = local_callers(workflows)
    for wf in workflows:
        env_scalars = list(iter_scalars(mget(wf.root, "env")))
        for job in wf.jobs.values():
            an = Analysis(wf, job)
            runs_on = mget(job.node, "runs-on")
            ro_text = flat(runs_on)
            for text, line, _raw in iter_scalars(runs_on):
                outside = EXPR_RE.sub(" ", text)
                for m in AWS_LABEL_RE.finditer(outside):
                    an.label(m.group(0), wf.path, line, wf.bad_events)
                for body in EXPR_RE.findall(text):
                    analyse_runs_on_expr(an, wf, job, body, line, callers)
            uses = sval(mget(job.node, "uses"))
            if uses:
                local = uses.startswith("./") and uses[2:].split("@", 1)[0] in {w.path for w in workflows}
                for text, line, _raw in iter_scalars(mget(job.node, "with")):
                    if ROUTE_RE.search(text):
                        an.route("reusable-workflow call passes a NEBBOS_CI_RUNNER variable")
                    for m in AWS_LABEL_RE.finditer(text):
                        an.route("reusable-workflow call passes a literal nebbos-ci-* AWS runner label")
                        if not local:  # a local callee is judged where its runs-on reads the input
                            in_expr = m.group(0) not in EXPR_RE.sub(" ", text)
                            an.label(m.group(0), wf.path, line, wf.bad_events, expr=in_expr)
            reason = an.reasons[0] if an.reasons else ""
            # a caller's matrix or input route set up through with/strategy, as before
            job.routed = bool(reason)
            job.route_reason = reason
            job.hard_ubuntu = (
                not reason
                and not uses
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

    seen: set[tuple[str, int, str]] = set()

    def add(f: Finding) -> None:
        key = (f.file, f.line, f.message)
        if key not in seen:
            seen.add(key)
            findings.append(f)

    for wf in workflows:
        for job in wf.jobs.values():
            where = f"{job.workflow}:{job.job_id}"
            for r in job.reaches:
                if r.in_expression:
                    add(Finding(
                        "error", r.file, r.line,
                        f"{where} names the literal label {ONDEMAND_PREFIX} inside an expression. The On-Demand pool is trusted "
                        f"(it writes the trusted/ cache prefix); route through the variable instead: "
                        f"runs-on: ${{{{ fromJSON(vars.NEBBOS_CI_RUNNER_QUEUE) }}}} on merge_group or main-only push.",
                    ))
                elif r.bad_events:
                    add(Finding(
                        "error", r.file, r.line,
                        f"{where} can run on {ONDEMAND_PREFIX} and is reachable from untrusted event(s) "
                        f"{', '.join(r.bad_events)}. The On-Demand pool is for merge_group and push to main only; "
                        f"use the variable routing (vars.NEBBOS_CI_RUNNER_QUEUE) or limit the workflow to those events.",
                    ))
            if wf.bad_events:
                for line, why in job.unresolved:
                    add(Finding("error", job.workflow, line, f"{where} {why} (untrusted event(s): {', '.join(wf.bad_events)})."))

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
