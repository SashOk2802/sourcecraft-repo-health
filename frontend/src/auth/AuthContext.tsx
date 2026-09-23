import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { fetchSession, signOut, yandexSignInUrl, type CurrentUser, type Session } from "../api/me";
import { mockSession } from "../api/mocks/session";
import { navigate } from "../router";

export interface AuthState {
  /** unknown — ещё не спросили backend. */
  status: "unknown" | "guest" | "signedIn";
  /** Кто обслуживает вход: backend, демо-кабинет или никто (вход недоступен). */
  mode: Session["mode"] | null;
  user: CurrentUser | null;
  /** Уводит на вход через Яндекс ID и после входа возвращает на returnTo. */
  signIn: (returnTo: string) => void;
  signOut: () => Promise<void>;
}

/** Что показать вместо входа, пока его нет ни у backend, ни в демо. */
export const signInUnavailableHint = "Вход через Яндекс ID появится, когда backend поднимет /api/v1/auth/yandex";

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);

  const loadSession = useCallback((): void => {
    void fetchSession().then(setSession);
  }, []);

  useEffect(loadSession, [loadSession]);

  const value = useMemo<AuthState>(
    () => ({
      status: session === null ? "unknown" : session.user ? "signedIn" : "guest",
      mode: session?.mode ?? null,
      user: session?.user ?? null,
      signIn: (returnTo) => {
        if (session?.mode === "demo") {
          mockSession.signIn();
          loadSession();
          navigate(returnTo);
          return;
        }
        if (session?.mode === "live") {
          window.location.assign(yandexSignInUrl(returnTo));
        }
      },
      signOut: async () => {
        await signOut();
        setSession((current) => (current ? { ...current, user: null } : current));
      },
    }),
    [session, loadSession],
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
