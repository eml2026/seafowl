"""Consistency checks for the nightly build/release workflow matrix."""

import unittest
from pathlib import Path

import yaml

WORKFLOW = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "nightly.yml"
)

# protoc release assets published for v21.4 that we rely on
PROTOC_BUILDS = {"linux-x86_64", "linux-aarch_64", "osx-x86_64", "osx-aarch_64"}

EXPECTED_BUILDS = {
    "linux-x86_64": {
        "os": "ubuntu-22.04",
        "target": "x86_64-unknown-linux-gnu",
        "protoc_build": "linux-x86_64",
    },
    "linux-arm64": {
        "os": "ubuntu-22.04-arm",
        "target": "aarch64-unknown-linux-gnu",
        "protoc_build": "linux-aarch_64",
    },
    "osx-x86_64": {
        "os": "macos-latest",
        "target": "x86_64-apple-darwin",
        "protoc_build": "osx-x86_64",
    },
}


class NightlyWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(WORKFLOW) as f:
            cls.workflow = yaml.safe_load(f)
        cls.build_job = cls.workflow["jobs"]["build_binary"]
        cls.matrix = cls.build_job["strategy"]["matrix"]
        cls.includes = {entry["build"]: entry for entry in cls.matrix["include"]}
        cls.release_job = cls.workflow["jobs"]["github_release"]

    def steps(self, job, name):
        return [step for step in job["steps"] if step.get("name") == name]

    def test_matrix_builds(self):
        self.assertEqual(set(self.matrix["build"]), set(EXPECTED_BUILDS))
        self.assertEqual(set(self.includes), set(EXPECTED_BUILDS))
        for build, expected in EXPECTED_BUILDS.items():
            with self.subTest(build=build):
                entry = self.includes[build]
                for key, value in expected.items():
                    self.assertEqual(entry[key], value)

    def test_every_build_has_an_available_protoc_asset(self):
        for build, entry in self.includes.items():
            with self.subTest(build=build):
                self.assertIn(entry["protoc_build"], PROTOC_BUILDS)

    def test_protoc_download_uses_the_protoc_build_name(self):
        (step,) = self.steps(self.build_job, "Install prerequisites")
        self.assertIn("protoc-21.4-${{ matrix.protoc_build }}.zip", step["run"])

    def test_artifacts_are_named_per_target(self):
        (step,) = self.steps(self.build_job, "Prepare artifact name")
        self.assertIn("ARTIFACT=seafowl-nightly-${{ matrix.target }}", step["run"])

    def test_docker_image_is_only_built_for_a_single_build(self):
        docker_steps = [
            step
            for step in self.build_job["steps"]
            if "Docker" in step.get("name", "") or "DockerHub" in step.get("name", "")
        ]
        self.assertTrue(docker_steps)
        for step in docker_steps:
            with self.subTest(step=step["name"]):
                self.assertEqual(step.get("if"), "matrix.build == 'linux-x86_64'")

    def test_release_packages_and_uploads_every_target(self):
        (package,) = self.steps(self.release_job, "Package artifacts")
        (upload,) = self.steps(self.release_job, "Upload release archive")
        files = upload["with"]["files"]
        for entry in self.includes.values():
            target = entry["target"]
            with self.subTest(target=target):
                self.assertIn(target, package["run"])
                self.assertIn(
                    f"seafowl-${{{{ env.RELEASE_VERSION }}}}-{target}.tar.gz", files
                )

    def test_release_waits_for_all_binaries(self):
        self.assertEqual(self.release_job["needs"], "build_binary")


if __name__ == "__main__":
    unittest.main()
