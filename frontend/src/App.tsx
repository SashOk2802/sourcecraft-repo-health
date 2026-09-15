import { useEffect, useState } from "react";

type Category = {
  code: string;
  label: string;
  score: number | null;
  summary: string;
};

const categories: Category[] = [
  { code: "security", label: "Безопасность", score: 61, summary: "Нужны обновления зависимостей." },
  { code: "cicd", label: "CI/CD", score: 84, summary: "Пайплайн выполняется стабильно." },
  { code: "quality", label: "Качество кода", score: 79, summary: "Линтер и тесты настроены." },
  { code: "dependencies", label: "Зависимости", score: null, summary: "Данные временно недоступны." },
];

export function App() {
  const [backendStatus, setBackendStatus] = useState("проверяем");

  useEffect(() => {
    void fetch("/api/v1/health")
      .then((response) => {
        if (!response.ok) throw new Error("Backend is unavailable");
        return response.json() as Promise<{ status: string }>;
      })
      .then(() => setBackendStatus("доступен"))
      .catch(() => setBackendStatus("недоступен"));
  }, []);

  return (
    <main>
      <header>
        <p>SourceCraft Repo Health</p>
        <h1>team/platform-api</h1>
        <strong>76 <small>/ 100</small></strong>
        <span>Backend: {backendStatus}</span>
      </header>
      <section>
        <h2>Категории оценки</h2>
        <div className="grid">
          {categories.map((category) => (
            <article key={category.code}>
              <h3>{category.label}</h3>
              <b>{category.score ?? "—"}</b>
              <p>{category.summary}</p>
            </article>
          ))}
        </div>
      </section>
      <section>
        <h2>Рекомендации</h2>
        <article>
          <h3>P0 · Обновить уязвимые зависимости</h3>
          <p>После обновления пакетов повторно запустите анализ.</p>
        </article>
      </section>
    </main>
  );
}
