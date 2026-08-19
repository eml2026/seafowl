"""Consistency checks for the nightly/release build matrix.

The nightly workflow only runs on pushes to main and on tags, so mistakes in it (a target
that is built but never released, a protoc archive that doesn't exist upstream, a Docker
architecture that never makes it into the manifest list) surface after a PR is merged.
These tests run in CI instead.
"""

import pathlib
import re
import unittest

import yaml

WORKFLOW_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / ".github" / "workflows" / "nightly.yml"
)

# Archives published in https://github.com/protocolbuffers/protobuf/releases/tag/v21.4
PROTOC_ARCHIVES = {
    "linux-aarch_64",
    "linux-x86_64",
    "osx-aarch_64",
    "osx-universal_binary",
    "osx-x86_64",
    "win64",
}

EXPECTED_TARGETS = {
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "x86_64-apple-darwin",
}

LINUX_CONDITION = "startsWith(matrix.build, 'linux-')"


def load_workflow():
    with WORKFLOW_PATH.open() as f:
        return yaml.safe_load(f)


class NightlyWorkflowTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = load_workflow()
        cls.raw = WORKFLOW_PATH.read_text()
        cls.build_job = cls.workflow["jobs"]["build_binary"]
        cls.matrix = cls.build_job["strategy"]["matrix"]
        cls.entries = {entry["build"]: entry for entry in cls.matrix["include"]}
        cls.release_job = cls.workflow["jobs"]["github_release"]

    def steps(self, job, needle):
        return [
            step
            for step in job["steps"]
            if needle in (step.get("name") or "") or needle in (step.get("uses") or "")
        ]

    def test_matrix_covers_all_expected_targets(self):
        self.assertEqual({e["target"] for e in self.entries.values()}, EXPECTED_TARGETS)

    def test_every_matrix_build_is_configured(self):
        self.assertEqual(set(self.matrix["build"]), set(self.entries))
        for build, entry in self.entries.items():
            with self.subTest(build=build):
                for key in ("os", "target", "protoc_arch"):
                    self.assertIn(key, entry)

    def test_linux_arm64_is_built_natively(self):
        entry = self.entries["linux-aarch64"]
        self.assertEqual(entry["target"], "aarch64-unknown-linux-gnu")
        # Cross-compiling would need a linker and a sysroot, so build on an arm64 runner
        self.assertTrue(entry["os"].endswith("-arm"), entry["os"])

    def test_protoc_archives_exist_upstream(self):
        for build, entry in self.entries.items():
            with self.subTest(build=build):
                self.assertIn(entry["protoc_arch"], PROTOC_ARCHIVES)

    def test_protoc_download_uses_the_matrix_arch(self):
        (step,) = self.steps(self.build_job, "Install prerequisites")
        self.assertIn("protoc-21.4-${{ matrix.protoc_arch }}.zip", step["run"])

    def test_docker_steps_run_for_every_linux_build(self):
        docker_steps = [
            step
            for step in self.build_job["steps"]
            if "docker" in (step.get("uses") or "").lower()
            or "Docker" in (step.get("name") or "")
        ]
        self.assertTrue(docker_steps)
        for step in docker_steps:
            with self.subTest(step=step.get("name")):
                self.assertEqual(step.get("if"), LINUX_CONDITION)

    def test_every_linux_build_has_a_docker_platform(self):
        platforms = {
            entry["build"]: entry.get("docker_platform")
            for entry in self.entries.values()
            if entry["build"].startswith("linux-")
        }
        self.assertEqual(
            platforms,
            {"linux-x86_64": "linux/amd64", "linux-aarch64": "linux/arm64"},
        )

    def test_per_arch_images_are_pushed_by_digest(self):
        (step,) = self.steps(self.build_job, "Build and push the Docker image by digest")
        self.assertEqual(step["with"]["platforms"], "${{ matrix.docker_platform }}")
        self.assertIn("push-by-digest=true", step["with"]["outputs"])
        # Tagging per architecture would make the architectures overwrite each other's tags
        self.assertNotIn("tags", step["with"])

    def test_digests_are_joined_into_a_multi_arch_manifest(self):
        job = self.workflow["jobs"]["docker_manifest"]
        self.assertIn("build_binary", job["needs"])
        digest_artifacts = self.steps(self.build_job, "Upload the Docker image digest")
        (upload,) = digest_artifacts
        (download,) = self.steps(job, "Get the per-architecture digests")
        self.assertTrue(
            re.fullmatch(
                download["with"]["pattern"].replace("*", ".*"),
                upload["with"]["name"].replace("${{ matrix.build }}", "linux-aarch64"),
            ),
            "the digest artifact name doesn't match the manifest job's download pattern",
        )
        (create,) = self.steps(job, "Create and push the manifest list")
        self.assertIn("docker buildx imagetools create", create["run"])

    def test_release_packages_and_uploads_every_target(self):
        (package,) = self.steps(self.release_job, "Package artifacts")
        (upload,) = self.steps(self.release_job, "Upload release archive")
        uploaded = set(
            re.findall(r"seafowl-\$\{\{ env\.RELEASE_VERSION }}-(\S+)\.tar\.gz", upload["with"]["files"])
        )
        self.assertEqual(uploaded, EXPECTED_TARGETS)
        for target in EXPECTED_TARGETS:
            with self.subTest(target=target):
                self.assertIn(target, package["run"])

    def test_release_downloads_the_nightly_artifacts(self):
        (download,) = self.steps(self.release_job, "Get artifacts")
        (name,) = self.steps(self.build_job, "Prepare artifact name")
        self.assertIn("ARTIFACT=seafowl-nightly-${{ matrix.target }}", name["run"])
        self.assertTrue(
            re.fullmatch(
                download["with"]["pattern"].replace("*", ".*"),
                "seafowl-nightly-aarch64-unknown-linux-gnu",
            )
        )


if __name__ == "__main__":
    unittest.main()
