import { Button, ClipboardButton, Label, Text, TextInput } from "@gravity-ui/uikit";
import { useState, type FormEvent, type ReactNode } from "react";

import { dataMode } from "../api/dataSource";
import { usePageMeta } from "../hooks/usePageMeta";
import { categoryStatusLabels } from "../lib/labels";
import { badgePath, formatApiBody, parseRepositorySlug, publicHealthPath, type RepositorySlug } from "../lib/publicApi";
import { Link } from "../router";
import { paths } from "../routes";
import "./PublicApiPage.css";

/** Та же документация в репозитории — для тех, кто читает код, а не сайт. */
const GITHUB_DOC_URL = "https://github.com/SashOk2802/sourcecraft-repo-health/blob/main/docs/public-api.md";

const example: RepositorySlug = { organization: "team", repository: "platform-api" };

// Формат ответа — backend/app/main.py, _public_repository_health_payload.
const exampleResponse = `{
  "repository": {
    "organizationSlug": "team",
    "repositorySlug": "platform-api",
    "url": "https://sourcecraft.dev/team/platform-api",
    "language": "Python"
  },
  "score": 82.5,
  "coverage": 1,
  "isPreliminary": false,
  "scoreLimited": false,
  "analyzedAt": "2026-09-29T12:00:00Z",
  "methodologyVersion": "v2",
  "categories": [
    { "code": "security", "label": "Безопасность", "status": "measured", "score": 90 },
    { "code": "cicd", "label": "CI/CD", "status": "unavailable", "score": null }
  ]
}`;

const sections = [
  { id: "request", title: "Запрос" },
  { id: "response", title: "Ответ" },
  { id: "codes", title: "Коды ответа и кеширование" },
  { id: "try", title: "Попробовать" },
  { id: "badge", title: "Бейдж для README" },
] as const;

export function PublicApiPage() {
  usePageMeta({
    title: "Публичный API",
    description:
      "Публичный API Repo Health: оценка здоровья открытого репозитория SourceCraft в JSON без ключа и SVG-бейдж для README.",
  });
  const origin = window.location.origin;
  const exampleUrl = `${origin}${publicHealthPath(example)}`;

  return (
    <div className="page__inner public-api">
      <div className="page__header">
        <div>
          <Text variant="header-2" as="h1" className="page__title">
            Публичный API
          </Text>
          <Text variant="body-2" color="secondary" className="page__lead">
            Оценку открытого репозитория SourceCraft можно встроить в сайт, дашборд или бота. API только читает данные,
            работает без ключа и входа и отвечает лишь для репозиториев, которые SourceCraft прямо сейчас подтверждает
            как публичные.
          </Text>
        </div>
        <Label size="m">Без ключа</Label>
      </div>

      <nav className="public-api__toc" aria-label="Разделы документации API">
        {sections.map((section, index) => (
          <a key={section.id} href={`#${section.id}`}>
            <span className="num">{index + 1}</span> {section.title}
          </a>
        ))}
      </nav>

      <Section index={0}>
        <p className="public-api__endpoint">
          <span className="public-api__method">GET</span>
          <code>/api/v1/public/repositories/{"{organization}"}/{"{repository}"}/health</code>
        </p>
        <Text variant="body-2">
          <code>organization</code> и <code>repository</code> — как в адресе репозитория на SourceCraft:
          sourcecraft.dev/<b>team</b>/<b>platform-api</b>.
        </Text>
        <CodeBlock code={`curl "${exampleUrl}"`} />
        <Text variant="body-2">
          Из браузера — обычный <code>fetch</code>: ответ разрешён для любых сайтов (
          <code>Access-Control-Allow-Origin: *</code>).
        </Text>
        <CodeBlock
          code={`const response = await fetch("${exampleUrl}");
if (!response.ok) throw new Error(\`Repo Health API: \${response.status}\`);
const health = await response.json();`}
        />
      </Section>

      <Section index={1}>
        <CodeBlock code={exampleResponse} />
        <table className="public-api__table">
          <thead>
            <tr>
              <th scope="col">Поле</th>
              <th scope="col">Что значит</th>
            </tr>
          </thead>
          <tbody>
            <FieldRow name="repository">организация, репозиторий, ссылка на SourceCraft и основной язык</FieldRow>
            <FieldRow name="score">
              Repo Health Score от 0 до 100; <code>null</code>, если ни одну категорию не удалось измерить
            </FieldRow>
            <FieldRow name="coverage">доля веса категорий с данными, от 0 до 1</FieldRow>
            <FieldRow name="isPreliminary">
              <code>true</code> — данных собрано не по всем категориям, оценка предварительная. Не выдавайте её за
              окончательную
            </FieldRow>
            <FieldRow name="scoreLimited">
              <code>true</code> — оценка ограничена значением 60 из-за подтверждённой критической уязвимости
            </FieldRow>
            <FieldRow name="analyzedAt">время анализа, ISO 8601 в UTC</FieldRow>
            <FieldRow name="methodologyVersion">
              версия методики. Веса и формула — <code>GET /api/v1/methodology</code> и страница{" "}
              <Link to={paths.methodology()}>«Как считаем»</Link>
            </FieldRow>
            <FieldRow name="categories">
              шесть категорий: <code>code</code>, <code>label</code>, <code>score</code> (0–100 или <code>null</code>) и{" "}
              <code>status</code>:{" "}
              {Object.entries(categoryStatusLabels)
                .map(([status, label]) => `${status} — ${label}`)
                .join(", ")}
              . Отсутствие данных не означает ноль баллов
            </FieldRow>
          </tbody>
        </table>
        <Text variant="body-1" color="secondary">
          Подробного отчёта, рекомендаций, фактов и идентификатора анализа в ответе нет: они могут быть личными и видны
          только тому, кто запускал анализ.
        </Text>
      </Section>

      <Section index={2}>
        <table className="public-api__table">
          <thead>
            <tr>
              <th scope="col">Код</th>
              <th scope="col">Что значит</th>
            </tr>
          </thead>
          <tbody>
            <FieldRow name="200">
              оценка найдена. Ответ можно кешировать пять минут: <code>Cache-Control: public, max-age=300</code>
            </FieldRow>
            <FieldRow name="404">
              репозитория нет, он не публичный или ещё не проанализирован. Причины намеренно не различаются: API не
              раскрывает, какие закрытые репозитории существуют
            </FieldRow>
            <FieldRow name="503">каталог SourceCraft не настроен или временно недоступен — повторите позже</FieldRow>
          </tbody>
        </table>
        <Text variant="body-1" color="secondary">
          Ошибки не кешируются (<code>no-store</code>): видимость репозитория и свежесть анализа могут измениться. Если
          репозиторий стал закрытым, следующий ответ уже не покажет сохранённую оценку.
        </Text>
      </Section>

      <Section index={3}>
        <TryRequest />
      </Section>

      <Section index={4}>
        <p className="public-api__endpoint">
          <span className="public-api__method">GET</span>
          <code>/api/v1/repositories/{"{organization}"}/{"{repository}"}/badge.svg</code>
        </p>
        <Text variant="body-2">
          SVG-бейдж «repo health | 82» в цвете оценки. Для закрытого или ещё не проанализированного репозитория —
          серый «unknown». Готовый фрагмент для README.md:
        </Text>
        <CodeBlock code={`[![Repo Health](${origin}${badgePath(example)})](${origin}/)`} />
        <Text variant="body-1" color="secondary">
          В отчёте любого публичного репозитория кнопка «Бейдж и API» даёт те же фрагменты уже с его адресом: Markdown,
          HTML и прямую ссылку.
        </Text>
      </Section>

      <Text variant="body-1" color="secondary" className="public-api__source">
        Та же документация в репозитории проекта:{" "}
        <a href={GITHUB_DOC_URL} target="_blank" rel="noreferrer">
          docs/public-api.md
        </a>
        .
      </Text>
    </div>
  );
}

function Section({ index, children }: { index: number; children: ReactNode }) {
  const section = sections[index];
  return (
    <section id={section.id} className="public-api__section" aria-labelledby={`${section.id}-title`}>
      <Text variant="display-1" color="secondary" className="public-api__number num">
        {String(index + 1).padStart(2, "0")}
      </Text>
      <div className="public-api__body">
        <Text variant="subheader-3" as="h2" id={`${section.id}-title`} className="public-api__heading">
          {section.title}
        </Text>
        {children}
      </div>
    </section>
  );
}

function FieldRow({ name, children }: { name: string; children: ReactNode }) {
  return (
    <tr>
      <th scope="row">
        <code>{name}</code>
      </th>
      <td>{children}</td>
    </tr>
  );
}

function CodeBlock({ code }: { code: string }) {
  return (
    <div className="public-api__code">
      <pre>
        <code>{code}</code>
      </pre>
      <ClipboardButton
        text={code}
        size="s"
        view="flat-secondary"
        tooltipInitialText="Скопировать"
        tooltipSuccessText="Скопировано"
        className="public-api__copy"
      />
    </div>
  );
}

type TryState =
  | { status: "idle" }
  | { status: "loading"; path: string }
  | { status: "done"; path: string; code: number; body: string }
  | { status: "failed"; path: string };

const codeMeanings: Record<number, string> = {
  200: "оценка найдена",
  404: "репозитория нет, он не публичный или ещё не проанализирован",
  503: "каталог SourceCraft не настроен или временно недоступен",
};

/** Живой запрос к API этого же сервера: видно настоящий ответ, код и заголовки не нужны. */
function TryRequest() {
  const [input, setInput] = useState("");
  const [state, setState] = useState<TryState>({ status: "idle" });
  const slug = parseRepositorySlug(input);
  // Демо-сборка работает без backend: запрос некуда отправить.
  const offline = dataMode === "demo";

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!slug || offline) return;
    const path = publicHealthPath(slug);
    setState({ status: "loading", path });
    try {
      const response = await fetch(path, { headers: { Accept: "application/json" } });
      setState({ status: "done", path, code: response.status, body: formatApiBody(await response.text()) });
    } catch {
      setState({ status: "failed", path });
    }
  }

  return (
    <>
      <Text variant="body-2">
        Введите репозиторий как <code>организация/репозиторий</code> или вставьте ссылку на него — запрос уйдёт в API
        этого сервера.
      </Text>
      <form className="public-api__try" onSubmit={(event) => void submit(event)}>
        <TextInput
          value={input}
          onUpdate={setInput}
          placeholder="team/platform-api"
          size="l"
          disabled={offline}
          validationState={input.trim() !== "" && !slug ? "invalid" : undefined}
          errorMessage="Нужно вида организация/репозиторий"
          controlProps={{ "aria-label": "Репозиторий SourceCraft" }}
        />
        <Button type="submit" view="action" size="l" disabled={!slug || offline} loading={state.status === "loading"}>
          Запросить
        </Button>
      </form>
      {offline && (
        <Text variant="body-1" color="secondary">
          Это демо-сборка без backend: живой запрос недоступен. На стенде он уходит в настоящий API.
        </Text>
      )}
      {state.status === "done" && (
        <div className="public-api__result" aria-live="polite">
          <Text variant="body-1" color="secondary" className="num">
            GET {state.path} → <b className={state.code === 200 ? "public-api__ok" : "public-api__fail"}>{state.code}</b>
            {codeMeanings[state.code] ? ` — ${codeMeanings[state.code]}` : ""}
          </Text>
          <CodeBlock code={state.body} />
        </div>
      )}
      {state.status === "failed" && (
        <Text variant="body-1" color="danger" aria-live="polite">
          Сервер не ответил. Проверьте подключение и попробуйте ещё раз.
        </Text>
      )}
    </>
  );
}
