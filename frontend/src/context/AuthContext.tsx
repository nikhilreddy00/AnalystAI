"use client";

import React, { createContext, useContext, useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";

export interface User {
  uid: string;
  email: string | null;
  displayName: string | null;
  token: string;
}

interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  loginWithGoogle: () => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  // Load user from localStorage on mount (for offline Mock mode persistence)
  useEffect(() => {
    const savedUser = localStorage.getItem("analystai_user");
    if (savedUser) {
      try {
        setUser(JSON.parse(savedUser));
      } catch (e) {
        console.error("Failed to parse saved user", e);
      }
    }
    setLoading(false);
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
    setLoading(true);
    try {
      // Mock / Dev auth: Simulates successful login and generates mock token
      const mockUid = email.split("@")[0] || "dev_user";
      const mockUser: User = {
        uid: mockUid,
        email,
        displayName: mockUid.toUpperCase(),
        token: `mock_${mockUid}`,
      };
      setUser(mockUser);
      localStorage.setItem("analystai_user", JSON.stringify(mockUser));
      router.push("/chat");
    } catch (error) {
      console.error("Login failed", error);
      throw error;
    } finally {
      setLoading(false);
    }
  };

  const register = async (email: string, password: string) => {
    setLoading(true);
    try {
      const mockUid = email.split("@")[0] || "dev_user";
      const mockUser: User = {
        uid: mockUid,
        email,
        displayName: mockUid.toUpperCase(),
        token: `mock_${mockUid}`,
      };
      setUser(mockUser);
      localStorage.setItem("analystai_user", JSON.stringify(mockUser));
      router.push("/chat");
    } catch (error) {
      console.error("Registration failed", error);
      throw error;
    } finally {
      setLoading(false);
    }
  };

  const loginWithGoogle = async () => {
    setLoading(true);
    try {
      const mockUser: User = {
        uid: "google_user",
        email: "google.user@example.com",
        displayName: "Google User",
        token: "mock_google_user",
      };
      setUser(mockUser);
      localStorage.setItem("analystai_user", JSON.stringify(mockUser));
      router.push("/chat");
    } catch (error) {
      console.error("Google Sign-In failed", error);
      throw error;
    } finally {
      setLoading(false);
    }
  };

  const logout = async () => {
    setUser(null);
    localStorage.removeItem("analystai_user");
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
