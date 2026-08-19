#!/usr/bin/env python3

"""Tests for the release/nightly build workflow.

These guard the build matrix against inconsistencies that would only surface on a push to
main or on a tag (i.e. after a PR has been merged), e.g. a target that is built but never
packaged into a release, or a protoc archive that doesn't exist upstream.
"""

import pathlib
import re
import unittest

import yaml

WORKFLOW = pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "nightly.yml"
PROTOC_VERSION = "21.4"

# Archives published in https://github.com/protocolbuffers/protobuf/releases/tag/v21.4
# (only the ones we could plausibly build on)
PROTOC_ARCHIVES = {
    "linux-x86_64",
    "linux-aarch_64",
    "osx-x86_64",
    "osx-aarch_64",
    "osx-universal_binary",
    "win64",
}


def load_workflow():
    with WORKFLOW.open() as fh:
        return yaml.safe_load(fh)


def build_matrix(workflow):
    """Return the build matrix as a {build name: build config} mapping."""
    matrix = workflow["jobs"]["build_binary"]["strategy"]["matrix"]
    builds = {entry["build"]: entry for entry in matrix["include"]}
    # The `build` axis and the `include` entries must describe the same set of builds,
    # otherwise the matrix expands into builds with missing keys.
    assert set(matrix["build"]) == set(builds), (
        f"Matrix builds {matrix['build']} don't match include entries {sorted(builds)}"
    )
    return builds


def job_steps(workflow, job):
    return workflow["jobs"][job]["steps"]


def step_with_name(workflow, job, name_fragment):
    for step in job_steps(workflow, job):
        if name_fragment in step.get("name", ""):
            return step
    raise AssertionError(f"No step matching {name_fragment!r} in job {job}")


class TestBuildMatrix(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow()
        self.builds = build_matrix(self.workflow)

    def test_linux_arm64_is_built(self):
        self.assertIn("linux-aarch64", self.builds)
        build = self.builds["linux-aarch64"]
        self.assertEqual(build["target"], "aarch64-unknown-linux-gnu")
        # Native arm64 runner, so that we can also run the tests/binary we build
        self.assertTrue(
            build["os"].endswith("-arm"),
            f"{build['os']} is not an arm64 runner",
        )
        self.assertEqual(build["docker_platform"], "linux/arm64")

    def test_every_build_is_fully_specified(self):
        for name, build in self.builds.items():
            with self.subTest(build=name):
                for key in ("os", "target", "protoc_arch"):
                    self.assertIn(key, build)

    def test_targets_are_unique(self):
        targets = [build["target"] for build in self.builds.values()]
        self.assertCountEqual(targets, set(targets))

    def test_protoc_archives_exist(self):
        for name, build in self.builds.items():
            with self.subTest(build=name):
                self.assertIn(build["protoc_arch"], PROTOC_ARCHIVES)

    def test_protoc_is_downloaded_for_the_build_architecture(self):
        step = step_with_name(self.workflow, "build_binary", "Install prerequisites")
        self.assertIn(
            f"protoc-{PROTOC_VERSION}-${{{{ matrix.protoc_arch }}}}.zip", step["run"]
        )

    def test_linux_builds_produce_docker_images(self):
        docker_steps = [
            step
            for step in job_steps(self.workflow, "build_binary")
            if "Docker" in step.get("name", "")
        ]
        self.assertTrue(docker_steps)
        for step in docker_steps:
            with self.subTest(step=step["name"]):
                # All Docker steps run for every Linux build, not just x86_64
                self.assertEqual(step.get("if"), "startsWith(matrix.build, 'linux-')")

        for name, build in self.builds.items():
            if name.startswith("linux-"):
                with self.subTest(build=name):
                    self.assertIn("docker_platform", build)


class TestDockerManifest(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow()
        self.builds = build_matrix(self.workflow)

    def test_manifest_waits_for_all_builds(self):
        self.assertEqual(self.workflow["jobs"]["docker_manifest"]["needs"], "build_binary")

    def test_manifest_collects_all_per_architecture_digests(self):
        upload = step_with_name(self.workflow, "build_binary", "Docker image digest as an")
        self.assertEqual(upload["with"]["name"], "docker-digest-${{ matrix.build }}")
        download = step_with_name(
            self.workflow, "docker_manifest", "per-architecture digests"
        )
        self.assertEqual(download["with"]["pattern"], "docker-digest-*")
        self.assertTrue(download["with"]["merge-multiple"])

    def test_manifest_is_tagged(self):
        meta = step_with_name(self.workflow, "docker_manifest", "Determine Docker tags")
        self.assertIn("nightly", meta["with"]["tags"])
        create = step_with_name(self.workflow, "docker_manifest", "manifest list")
        self.assertIn("docker buildx imagetools create", create["run"])


class TestGitHubRelease(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow()
        self.builds = build_matrix(self.workflow)

    def test_every_built_target_is_packaged_and_released(self):
        package = step_with_name(self.workflow, "github_release", "Package artifacts")
        packaged = set(re.findall(r"\w+-(?:unknown-linux-gnu|apple-darwin)", package["run"]))
        upload = step_with_name(self.workflow, "github_release", "Upload release archive")
        released = set(
            re.findall(
                r"-(\w+-(?:unknown-linux-gnu|apple-darwin))\.tar\.gz",
                upload["with"]["files"],
            )
        )
        expected = {build["target"] for build in self.builds.values()}
        self.assertSetEqual(packaged, expected)
        self.assertSetEqual(released, expected)


if __name__ == "__main__":
    unittest.main()
