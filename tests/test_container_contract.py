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
