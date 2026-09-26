# Как добавлять тесты и проходить CI

CI — это автоматическая проверка GitHub Actions. Она запускается после каждого push в ветку и при открытии или обновлении pull request. Если проверка красная, исправляет её автор своей ветки до merge.

## Что уже проверяет CI

| Проверка | Что делает |
|---|---|
| Backend | Запускает все Python-тесты из `backend/tests` и Ruff — проверку стиля и простых ошибок. |
| Frontend | Проверяет TypeScript, запускает Vitest-тесты и собирает production-версию интерфейса. |
| Docker Compose | Проверяет, что `compose.yaml` корректно описывает окружение. |

CI не запускает настоящее сканирование SourceCraft, не обращается к интернету и не использует Redis. Для PostgreSQL-интеграционных тестов GitHub Actions поднимает отдельную временную базу и применяет миграции Alembic. Тесты должны работать изолированно и быстро.

## Перед созданием pull request

Запускайте проверки только для той части, которую меняли.

### Backend

Один раз установите Python-зависимости:

~~~powershell
python -m pip install -e ".[dev]"
~~~

После изменений backend, затрагивающих PostgreSQL:

~~~powershell
alembic upgrade head
python -m pytest
ruff check backend --ignore EXE002
~~~

Если используете Docker Desktop, команды можно выполнять в том же контейнере, что и backend:

~~~powershell
docker compose run --rm --no-deps backend alembic upgrade head
docker compose run --rm --no-deps backend python -m pytest
docker compose run --rm --no-deps backend ruff check backend --ignore EXE002
~~~

### Frontend

~~~powershell
cd frontend
npm ci
npm run check
npm test
npm run build
~~~

Можно проверить это через Docker:

~~~powershell
docker compose run --rm --no-deps frontend npm run check
docker compose run --rm --no-deps frontend npm run build
~~~

### Docker Compose

После изменения `compose.yaml`, Dockerfile или переменных окружения:

~~~powershell
docker compose config
~~~

## Как писать backend-тесты

1. Вместе с изменением поведения добавьте или обновите тест в `backend/tests/test_<модуль>.py`.
2. Проверяйте успешный сценарий и один важный крайний случай: пустые данные, ошибка API, неверный статус или ограничение Score.
3. Тест не должен обращаться в настоящий SourceCraft, GitHub или интернет. Для HTTP-клиента подменяйте транспорт через `httpx.MockTransport`.
4. Тестовые данные держите маленькими и понятными. По названию теста должно быть ясно, какое правило он проверяет.
5. Если исправляете ошибку, сначала добавьте тест, который воспроизводит её. Затем исправьте код, чтобы тест стал зелёным.

Пример структуры:

~~~text
backend/
  app/
    analyzers/
      activity.py
  tests/
    test_activity.py
~~~

Пример минимального теста:

~~~python
import unittest

from backend.app.contracts import DataStatus


class ActivityTest(unittest.TestCase):
    def test_empty_history_is_not_measured(self) -> None:
        status = DataStatus.INSUFFICIENT_SAMPLE

        self.assertEqual(status, DataStatus.INSUFFICIENT_SAMPLE)
~~~

Не копируйте этот пример буквально: тест должен вызывать вашу функцию или анализатор и проверять его результат.

## Что тестировать на frontend

CI уже ловит ошибки типов и ошибки сборки, а также запускает Vitest-тесты. Тестируйте важную логику: преобразование API-данных в модель интерфейса, расчёты для отображения (например, метка «Предварительно» для неполной оценки), фильтры, score bands и feature flags. Не нужно писать тест на каждую CSS-строку.

## Чек-лист автора PR

- [ ] Ветка обновлена от `main`.
- [ ] В PR есть небольшая законченная задача.
- [ ] Для нового или изменённого backend-поведения есть тест.
- [ ] Локальные команды из раздела выше прошли.
- [ ] В описании PR указано, что изменилось и какими командами это проверено.
- [ ] После push все статусы CI зелёные.
- [ ] В `main` ничего не пушится напрямую: merge выполняется только через PR.

## Если проверка не проходит

1. Откройте в PR вкладку **Checks** и найдите упавший шаг.
2. Прочитайте первую понятную ошибку в логе: она обычно указывает файл и строку.
3. Исправьте проблему, запустите ту же команду локально и сделайте новый commit в свою ветку.
4. GitHub Actions перезапустится автоматически.

Если Docker не запускается на вашем компьютере, выполните доступные Python- или npm-проверки локально и напишите об этом в PR. Docker Compose и связку сервисов перед merge проверю я.
