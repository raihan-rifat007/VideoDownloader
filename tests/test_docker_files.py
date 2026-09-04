import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DockerFilesTest(unittest.TestCase):
    def test_entrypoint_uses_unix_line_endings(self):
        entrypoint = (PROJECT_ROOT / "docker-entrypoint.sh").read_bytes()

        self.assertNotIn(
            b"\r\n",
            entrypoint,
            "docker-entrypoint.sh must use LF line endings for Debian /bin/sh",
        )
        self.assertTrue(entrypoint.startswith(b"#!/bin/sh\n"))
        self.assertIn(b'exec "$@"', entrypoint)

    def test_git_keeps_shell_scripts_lf_on_every_host(self):
        attributes = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8")

        self.assertIn("*.sh text eol=lf", attributes.splitlines())

    def test_docker_build_normalizes_windows_checkout_without_changing_linux_entrypoint(self):
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertIn("sed -i 's/\\r$//' /app/docker-entrypoint.sh", dockerfile)
        self.assertIn(
            'ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]',
            dockerfile,
        )
        self.assertIn("apt-get install -y --no-install-recommends ffmpeg", dockerfile)

    def test_docker_image_requires_bilibili_fixed_ytdlp_release(self):
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8")
        entrypoint = (PROJECT_ROOT / "docker-entrypoint.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("yt-dlp>=2026.8.19", requirements.splitlines())
        self.assertIn('YT_DLP_REQUIREMENT="yt-dlp>=2026.8.19"', entrypoint)
        self.assertIn('yt-dlp --version', entrypoint)

    def test_cookie_export_is_ignored_by_git(self):
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("cookies.txt", gitignore.splitlines())

    def test_compose_passes_ytdlp_runtime_settings(self):
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text(
            encoding="utf-8"
        )

        for name in (
            "YTDLP_COOKIES_FILE",
            "YTDLP_PROXY",
            "YTDLP_USER_AGENT",
        ):
            with self.subTest(name=name):
                self.assertIn(f"{name}: ${{{name}:-}}", compose)


if __name__ == "__main__":
    unittest.main()
