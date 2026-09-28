# CI в SourceCraft

Файл `.sourcecraft/ci.yaml` запускает переносимую часть проверок проекта в
SourceCraft на каждый pull request в `main` и после push в `main`.

Четыре задачи выполняются независимо:

- backend: unit-тесты и Ruff;
- frontend: TypeScript, Vitest и production build;
- Python security: Bandit и `pip-audit`;
- frontend security: `npm audit` по lock-файлу.

Docker-образы закреплены по digest. Конфигурация не использует PAT,
`SOURCECRAFT_TOKEN`, пользовательские secrets, приватные slug или URL. Встроенные
SAST, SCA и secret scanning SourceCraft продолжают работать отдельно от этого
workflow.

Проверки PostgreSQL и Docker Compose остаются в GitHub Actions: им нужны service
containers и Docker daemon. SourceCraft CI дополняет их и проверяет те части,
которые воспроизводятся в обычных контейнерных cubes.

Тест `backend/tests/test_sourcecraft_ci_config.py` фиксирует триггеры, обязательные
команды, digest образов и отсутствие credential-маркеров. После merge файла в
default-ветку SourceCraft начнёт автоматически создавать запуски для новых PR.
