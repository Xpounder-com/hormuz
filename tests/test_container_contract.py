from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ContainerContractTests(unittest.TestCase):
    def test_image_pins_base_and_runtime_dependencies(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertRegex(
            dockerfile,
            r"python:3\.14\.6-alpine3\.23@sha256:[0-9a-f]{64}",
        )
        self.assertIn("--require-hashes", dockerfile)
        self.assertIn("--only-binary=:all:", dockerfile)
        self.assertIn("org.opencontainers.image.version", dockerfile)

        lock = (ROOT / "deploy/container/requirements.lock").read_text()
        self.assertIn("pyjwt==2.13.0", lock)
        self.assertIn("keyring==25.7.0", lock)
        requirements = [
            line for line in lock.splitlines() if re.match(r"^[a-z0-9-]+==", line)
        ]
        self.assertTrue(requirements)
        self.assertNotIn("~=", lock)
        self.assertNotRegex(lock, r"(?m)^[a-z0-9-]+>=")
        for requirement in requirements:
            self.assertIn("--hash=sha256:", requirement + "\n" + lock.split(
                requirement + "\n", 1
            )[1].split("\n", 3)[0])

    def test_runtime_lock_closes_python_311_conditional_dependency(self) -> None:
        lock = (ROOT / "deploy/container/requirements.lock").read_text()

        self.assertIn(
            "backports-tarfile==1.2.0 ; python_full_version < '3.12' \\",
            lock,
        )
        self.assertIn(
            "--hash=sha256:77e284d754527b01fb1e6fa8a1afe577858ebe4e9dad8919e34c862cb399bc34",
            lock,
        )
        self.assertIn(
            "--hash=sha256:d75e02c268746e1b8144c278978b6e98e85de6ad16f8e4b0844a154557eca991",
            lock,
        )
        self.assertIn(
            "importlib-metadata==9.0.0 ; python_full_version < '3.12' \\",
            lock,
        )
        self.assertIn(
            "zipp==4.1.0 ; python_full_version < '3.12' \\",
            lock,
        )
        container_doc = (ROOT / "docs/CONTAINER.md").read_text()
        self.assertIn("--universal", container_doc)
        self.assertIn("--python-version 3.11", container_doc)

    def test_image_declares_non_root_health_and_data_contract(self) -> None:
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("USER 65532:65532", dockerfile)
        self.assertIn('VOLUME ["/var/lib/hormuz"]', dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/health/ready", dockerfile)
        self.assertIn(
            'CMD ["--config", "/etc/hormuz/hormuz.json", "serve"]',
            dockerfile,
        )

    def test_build_context_is_default_deny(self) -> None:
        ignored = (ROOT / ".dockerignore").read_text().splitlines()
        self.assertEqual(ignored[0], "**")
        self.assertIn("!deploy/container/requirements.lock", ignored)
        self.assertIn("!hormuz/**", ignored)
        self.assertIn("hormuz/**/__pycache__/", ignored)
        self.assertIn("hormuz/**/*.pyc", ignored)


if __name__ == "__main__":
    unittest.main()
