"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import type { Session } from "@supabase/supabase-js";

import { supabase } from "@/lib/supabase";

export interface User {
  uid: string;
  email: string | null;
  displayName: string | null;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<boolean>;
  loginWithGoogle: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

function toUser(session: Session | null): User | null {
  if (!session?.user) return null;
  const meta = session.user.user_metadata ?? {};
  return {
    uid: session.user.id,
    email: session.user.email ?? null,
    displayName: (meta.full_name as string) ?? (meta.name as string) ?? null,
  };
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  // Supabase owns session persistence and token refresh. onAuthStateChange
  // fires once on mount with the restored session, so this covers both the
  // initial load and every later sign-in/sign-out/refresh.
  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setUser(toUser(data.session));
      setLoading(false);
    });

    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      setUser(toUser(session));
      setLoading(false);
    });

    return () => sub.subscription.unsubscribe();
  }, []);

  // Route protection redirect guard
  useEffect(() => {
    if (!loading) {
      const publicPaths = ["/auth", "/"];
      const isPublicPath = publicPaths.includes(pathname || "");
      if (!user && !isPublicPath) {
        router.push("/auth");
      } else if (user && pathname === "/auth") {
        router.push("/chat");
      }
    }
  }, [user, loading, pathname, router]);

  const login = async (email: string, password: string) => {
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    if (error) throw new Error(error.message);
    router.push("/chat");
  };

  /** Resolves true when the user must confirm their email before signing in. */
  const register = async (email: string, password: string) => {
    const { data, error } = await supabase.auth.signUp({ email, password });
    if (error) throw new Error(error.message);
    // With email confirmation enabled there is no session yet.
    if (!data.session) return true;
    router.push("/chat");
    return false;
  };

  const loginWithGoogle = async () => {
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${window.location.origin}/chat` },
    });
    if (error) throw new Error(error.message);
    // signInWithOAuth navigates away; nothing to do here.
  };

  const logout = async () => {
    // On a network/5xx failure signOut returns an error WITHOUT clearing the
    // stored session — drop it locally so a reload can't resurrect the user.
    const { error } = await supabase.auth.signOut();
    if (error) await supabase.auth.signOut({ scope: "local" });
    setUser(null);
    router.push("/auth");
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        login,
        register,
        loginWithGoogle,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
