import os
import shutil
import tempfile
import subprocess
from typing import Optional

class LocalGitRepository:
    """Управляет временной рабочей областью для анализа файлов репозитория."""
    
    def __init__(self, repo_url: str, branch: str = "main"):
        self.repo_url = repo_url
        self.branch = branch
        self.temp_dir: Optional[str] = None

    def clone(self) -> str:
        """Клонирует репозиторий (только 1 последний коммит для скорости) во временную папку."""
        self.temp_dir = tempfile.mkdtemp()
        try:
            subprocess.run(
                ["git", "clone", "--branch", self.branch, "--depth", "1", self.repo_url, self.temp_dir],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return self.temp_dir
        except subprocess.CalledProcessError as e:
            self.cleanup()
            raise RuntimeError(f"Не удалось клонировать репозиторий {self.repo_url}: {e}")

    def file_exists(self, relative_path: str) -> bool:
        """Проверяет наличие файла по относительному пути от корня репозитория."""
        if not self.temp_dir:
            return False
        return os.path.exists(os.path.join(self.temp_dir, relative_path))

    def read_file(self, relative_path: str) -> Optional[str]:
        """Безопасно читает содержимое файла."""
        if not self.file_exists(relative_path):
            return None
        full_path = os.path.join(self.temp_dir, relative_path)
        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except Exception:
            return None

    def cleanup(self) -> None:
        """Удаляет временную папку. Обязательно к вызову после завершения анализа!"""
        if self.temp_dir and os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)
