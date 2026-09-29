import { Flask } from "@gravity-ui/icons";
import { Icon } from "@gravity-ui/uikit";
import type { ReactNode } from "react";

import { cn } from "../lib/classNames";
import "./DemoNote.css";

/*
 * Пометка «это пример»: на стенде разделы, которых у backend ещё нет, работают
 * на вымышленных репозиториях. Посетитель должен понимать это сразу, а не по мелкому шрифту.
 */
export function DemoNote({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <p className={cn("demo-note", className)} role="note">
      <span className="demo-note__badge">
        <Icon data={Flask} size={14} />
        Демо
      </span>
      <span className="demo-note__text">{children}</span>
    </p>
  );
}
