import { Alert, Button, Loader, Text } from "@gravity-ui/uikit";
import type { ReactNode } from "react";

import { describeError } from "../api/http";
import "./PageNotes.css";

export function LoadingNote({ children }: { children: ReactNode }) {
  return (
    <div className="page-note" role="status">
      <Loader size="s" />
      <Text variant="body-2" color="secondary">
        {children}
      </Text>
    </div>
  );
}

interface ErrorNoteProps {
  title: string;
  error: Error;
  onRetry?: () => void;
}

export function ErrorNote({ title, error, onRetry }: ErrorNoteProps) {
  return (
    <Alert
      theme="danger"
      view="outlined"
      title={title}
      message={describeError(error)}
      actions={
        onRetry ? (
          <Button view="outlined" onClick={onRetry}>
            Попробовать ещё раз
          </Button>
        ) : undefined
      }
    />
  );
}
