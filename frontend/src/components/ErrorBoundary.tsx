import { Button, Text } from "@gravity-ui/uikit";
import { Component, type ErrorInfo, type ReactNode } from "react";

import "./ErrorBoundary.css";

interface ErrorBoundaryProps {
  children: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/*
 * Если страница упала при отрисовке — например, backend прислал отчёт в неожиданном
 * формате, — шапка и навигация остаются, а вместо белого экрана видно, что делать дальше.
 * В App граница пересоздаётся при переходе на другой адрес.
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Для разработчика: в консоли видно, какой компонент упал.
    console.error("Страница упала при отрисовке", error, info.componentStack);
  }

  render(): ReactNode {
    if (!this.state.error) {
      return this.props.children;
    }

    return (
      <div className="page__inner">
        <section className="card page-crash" role="alert">
          <Text variant="header-2" as="h1">
            Страница не открылась
          </Text>
          <Text variant="body-2" color="secondary">
            Что-то пошло не так при показе этой страницы. Остальные разделы работают — попробуйте обновить страницу
            или вернуться к рейтингу.
          </Text>
          <div className="page-crash__actions">
            <Button view="action" size="l" onClick={() => window.location.reload()}>
              Обновить страницу
            </Button>
            <Button view="outlined" size="l" href="/">
              К рейтингу
            </Button>
          </div>
        </section>
      </div>
    );
  }
}
