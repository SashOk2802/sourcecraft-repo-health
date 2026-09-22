import os
import shutil
import subprocess
import tempfile


class LocalGitRepository:
    """Управляет временной рабочей областью для анализа файлов репозитория."""

    def __init__(self, repo_url: str, ref: str | None = None):
        """Принимает URL и ссылку для checkout.

        ``ref`` может быть именем ветки, тегом или полным commit SHA.
        """
        self.repo_url = repo_url
        self.ref = ref
        self.temp_dir: str | None = None

    def clone(self) -> str:
        """Клонирует репозиторий во временную папку и переключается на ``ref``.

        Для веток и тегов используется shallow clone этой ссылки. Если ``ref``
        оказался commit SHA (например, ``AnalysisContext.commit_sha``), а не именем
        ветки, клонируем дефолтную ветку поверхностно и подтягиваем нужный коммит
        адресно (работает при поддержке сервера, как в GitHub/GitLab).
        """
        self.temp_dir = tempfile.mkdtemp()
        try:
            if self.ref:
                self._clone_shallow_with_ref(self.ref)
            else:
                self._run_git(["clone", "--depth", "1", self.repo_url, self.temp_dir])
            return self.temp_dir
        except subprocess.CalledProcessError as error:
            self.cleanup()
            raise RuntimeError(f"Не удалось клонировать репозиторий {self.repo_url}: {error}")

    def _clone_shallow_with_ref(self, ref: str) -> None:
        try:
            self._run_git(
                ["clone", "--branch", ref, "--depth", "1", self.repo_url, self.temp_dir]
            )
            return
        except subprocess.CalledProcessError:
            # ref, вероятно, commit SHA, а не ветка/тег: клонируем дефолтную ветку
            # и подтягиваем нужный коммит адресно.
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir = tempfile.mkdtemp()
            self._run_git(["clone", "--depth", "1", self.repo_url, self.temp_dir])
            self._run_git(["-C", self.temp_dir, "fetch", "--depth", "1", "origin", ref])
            self._run_git(["-C", self.temp_dir, "checkout", "--detach", "FETCH_HEAD"])

    @staticmethod
    def _run_git(arguments: list[str]) -> None:
        subprocess.run(
            ["git", *arguments],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def file_exists(self, relative_path: str) -> bool:
        """Проверяет наличие файла по относительному пути от корня репозитория."""
        if not self.temp_dir:
            return False
        return os.path.exists(os.path.join(self.temp_dir, relative_path))

    def read_file(self, relative_path: str) -> str | None:
        """Безопасно читает содержимое файла."""
        if not self.file_exists(relative_path):
            return None
        full_path = os.path.join(self.temp_dir, relative_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except OSError:
            return None

    def cleanup(self) -> None:
        """Удаляет временную папку. Обязательно к вызову после завершения анализа!"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
