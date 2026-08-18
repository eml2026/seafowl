#!/bin/bash -e

python3 - .github/workflows/nightly.yml <<'PY'
import re
import sys


workflow_path = sys.argv[1]
with open(workflow_path, encoding="utf-8") as workflow_file:
    workflow = workflow_file.read()


def job_body(job_name):
    match = re.search(
        rf"(?ms)^  {re.escape(job_name)}:\n.*?(?=^  [A-Za-z0-9_-]+:|\Z)",
        workflow,
    )
    if not match:
        raise SystemExit(f"missing job: {job_name}")
    return match.group()


build_binary = job_body("build_binary")
github_release = job_body("github_release")
targets = re.findall(r"(?m)^\s+target:\s*([A-Za-z0-9_.-]+)\s*$", build_binary)
if not targets:
    raise SystemExit("no build_binary matrix targets found")

package_step = re.search(
    r"(?ms)^      - name: Package artifacts\n.*?(?=^      - name:|\Z)",
    github_release,
)
upload_step = re.search(
    r"(?ms)^      - name: Upload release archive\n.*?(?=^      - name:|\Z)",
    github_release,
)
if not package_step or not upload_step:
    raise SystemExit("missing release packaging or upload step")

archive_prefix = r"seafowl-\$\{\{ env\.RELEASE_VERSION \}\}-"
package_loop = re.search(
    r"(?m)^\s*for target in ([^;]+); do\s*$",
    package_step.group(),
)
package_loop_targets = set()
if package_loop:
    package_loop_targets = set(re.findall(r"[A-Za-z0-9_.-]+", package_loop.group(1)))

package_template = re.search(
    r"seafowl-\$(?:\{\{ env\.RELEASE_VERSION \}\}|\{RELEASE_VERSION\})-\$\{target\}\.tar\.gz",
    package_step.group(),
)
for target in targets:
    archive = rf"{archive_prefix}{re.escape(target)}\.tar\.gz"
    packaged_literally = re.search(archive, package_step.group())
    packaged_in_loop = package_template and target in package_loop_targets
    if not (packaged_literally or packaged_in_loop):
        raise SystemExit(f"missing release packaging for target: {target}")
    if not re.search(archive, upload_step.group()):
        raise SystemExit(f"missing release upload for target: {target}")

print(f"nightly release artifacts cover {len(targets)} build targets: {', '.join(targets)}")
PY
