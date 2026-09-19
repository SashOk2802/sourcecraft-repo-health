import type { CurrentUser } from "../me";
import { readItem, removeItem, writeItem } from "./storage";

const SIGNED_IN_KEY = "repo-health:mock-signed-in";

// Для демо и скриншотов: ?mock-user=1 в адресе сразу «входит» в mock-режиме.
if (typeof window !== "undefined" && new URLSearchParams(window.location.search).get("mock-user") === "1") {
  writeItem(SIGNED_IN_KEY, "1");
}

const mockUser: CurrentUser = {
  id: "user-7",
  displayName: "Анна Ковалёва",
  login: "a.kovaleva",
  avatarUrl: null,
};

/** Имитация входа через Яндекс ID в mock-режиме. */
export const mockSession = {
  isSignedIn(): boolean {
    return readItem(SIGNED_IN_KEY) === "1";
  },
  user(): CurrentUser | null {
    return this.isSignedIn() ? mockUser : null;
  },
  signIn(): void {
    writeItem(SIGNED_IN_KEY, "1");
  },
  signOut(): void {
    removeItem(SIGNED_IN_KEY);
  },
};
