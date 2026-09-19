import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { mocksEnabled } from "../api/mockMode";
import { fetchCurrentUser, signOut, yandexSignInUrl, type CurrentUser } from "../api/me";
import { mockSession } from "../api/mocks/session";
import { navigate } from "../router";

export interface AuthState {
  /** unknown — ещё не спросили backend. */
  status: "unknown" | "guest" | "signedIn";
  user: CurrentUser | null;
  /** Уводит на вход через Яндекс ID и после входа возвращает на returnTo. */
  signIn: (returnTo: string) => void;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [status, setStatus] = useState<AuthState["status"]>("unknown");

  const loadUser = useCallback((): void => {
    fetchCurrentUser().then(
      (current) => {
        setUser(current);
        setStatus(current ? "signedIn" : "guest");
      },
      // Backend недоступен: работаем как с гостем, публичные страницы от этого не ломаются.
      () => {
        setUser(null);
        setStatus("guest");
      },
    );
  }, []);

  useEffect(loadUser, [loadUser]);

  const value = useMemo<AuthState>(
    () => ({
      status,
      user,
      signIn: (returnTo) => {
        if (mocksEnabled) {
          mockSession.signIn();
          loadUser();
          navigate(returnTo);
          return;
        }
        window.location.assign(yandexSignInUrl(returnTo));
      },
      signOut: async () => {
        await signOut();
        setUser(null);
        setStatus("guest");
      },
    }),
    [status, user, loadUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth можно вызывать только внутри AuthProvider");
  }
  return value;
}
