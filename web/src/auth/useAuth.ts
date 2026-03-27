import type { User } from "oidc-client-ts";
import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { signOutRedirect, userManager } from "./oidc";

interface AuthState {
  user: User | null;
  isLoading: boolean;
  error: string | null;
}

interface AuthContextValue extends AuthState {
  signIn: () => Promise<void>;
  signOut: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ user: null, isLoading: true, error: null });

  useEffect(() => {
    const onUserLoaded = (user: User) => setState({ user, isLoading: false, error: null });
    const onUserUnloaded = () => setState((s) => ({ ...s, user: null }));
    const onSilentRenewError = (err: Error) => setState((s) => ({ ...s, error: err.message }));

    userManager.events.addUserLoaded(onUserLoaded);
    userManager.events.addUserUnloaded(onUserUnloaded);
    userManager.events.addSilentRenewError(onSilentRenewError);

    void (async () => {
      try {
        if (window.location.pathname === "/callback") {
          const user = await userManager.signinRedirectCallback();
          window.history.replaceState({}, "", "/");
          setState({ user, isLoading: false, error: null });
          return;
        }
        const user = await userManager.getUser();
        setState({ user: user?.expired === false ? user : null, isLoading: false, error: null });
      } catch (err) {
        setState({ user: null, isLoading: false, error: (err as Error).message });
      }
    })();

    return () => {
      userManager.events.removeUserLoaded(onUserLoaded);
      userManager.events.removeUserUnloaded(onUserUnloaded);
      userManager.events.removeSilentRenewError(onSilentRenewError);
    };
  }, []);

  const value: AuthContextValue = {
    ...state,
    signIn: () => userManager.signinRedirect(),
    signOut: () => {
      void userManager.removeUser().finally(signOutRedirect);
    },
  };

  return createElement(AuthContext.Provider, { value }, children);
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
