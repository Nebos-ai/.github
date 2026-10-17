#!/usr/bin/env python3
"""Validate .nebbos/automations.yaml against schemas/automations-manifest.schema.json.

Beyond the JSON schema it checks: repo matches the repository being checked, every slug is
<repo-short>.<kebab-name> with repo-short derived from the repo name, slugs are unique,
a schedule means an expected interval, and each source_path exists in the checkout.

Usage: validate_automations_manifest.py <manifest> <schema> <repo-name> [<repo-root>]
Writes the manifest as JSON to the path in env MANIFEST_JSON_OUT when set. Exits 1 on any error.
"""
from __future__ import annotations

import json
import os
import re
import sys

import jsonschema
import yaml


def repo_short(repo: str) -> str:
    for prefix in ("nebos-", "nebbos-"):
        if repo.startswith(prefix):
            return repo[len(prefix):]
    return repo


def main(argv: list[str]) -> int:
    manifest_path, schema_path, repo = argv[1], argv[2], argv[3]
    root = argv[4] if len(argv) > 4 else "."
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = yaml.safe_load(fh)
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)

    errors: list[str] = []
    validator = jsonschema.Draft202012Validator(schema)
    for err in sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in err.absolute_path) or "(root)"
        errors.append(f"{loc}: {err.message}")

    if isinstance(manifest, dict) and not errors:
        if manifest["repo"] != repo:
            errors.append(f"repo: manifest says '{manifest['repo']}' but this repository is '{repo}'")
        short = repo_short(repo)
        seen: set[str] = set()
        for i, a in enumerate(manifest["automations"]):
            slug = a["slug"]
            if not slug.startswith(short + "."):
                errors.append(f"automations[{i}].slug: '{slug}' must start with '{short}.'")
            if slug in seen:
                errors.append(f"automations[{i}].slug: duplicate '{slug}'")
            seen.add(slug)
            if a["schedule"] is not None and a["expected_interval_minutes"] is None:
                errors.append(f"automations[{i}] ({slug}): a schedule needs expected_interval_minutes")
            if a["schedule"] is not None and len(a["schedule"].split()) != 5:
                errors.append(f"automations[{i}] ({slug}): schedule must be a 5-field cron expression")
            src = re.split(r":\d+$", a["source_path"])[0]
            # Only repo-relative paths are checked; anything with spaces, ~ or a URL scheme is descriptive.
            if re.fullmatch(r"[\w./@\[\]()\-]+", src) and not src.startswith(("~", "/")):
                if not os.path.exists(os.path.join(root, src)):
                    errors.append(f"automations[{i}] ({slug}): source_path '{src}' does not exist in this checkout")

    if errors:
        for e in errors:
            print(f"::error file={manifest_path}::{e}")
        print(f"{len(errors)} manifest error(s)")
        return 1

    out = os.environ.get("MANIFEST_JSON_OUT")
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
    print(f"manifest ok: {len(manifest['automations'])} automations for {repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
