"use client";

import React, { useState } from "react";
import { useAuth } from "@/context/AuthContext";

export default function AuthPage() {
  const { login, register, loginWithGoogle } = useAuth();
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setNotice(null);
    if (!email || !password) {
      setError("Please fill in all fields.");
      return;
    }
    setLoading(true);
    try {
      if (isLogin) {
        await login(email, password);
      } else {
        if (await register(email, password)) {
          setNotice(`Check ${email} for a confirmation link, then sign in.`);
          setIsLogin(true);
        }
      }
    } catch (err: any) {
      setError(err?.message || "Authentication failed. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 flex items-center justify-center p-6 relative overflow-hidden">
      {/* Background ambient light */}
      <div className="absolute top-1/4 left-1/4 -translate-x-1/2 -translate-y-1/2 w-96 h-96 rounded-full bg-indigo-500/10 blur-[128px] pointer-events-none" />
      <div className="absolute bottom-1/4 right-1/4 translate-x-1/2 translate-y-1/2 w-96 h-96 rounded-full bg-purple-500/10 blur-[128px] pointer-events-none" />

      {/* Main Card */}
      <div className="w-full max-w-md relative z-10">
        <div className="rounded-3xl border border-zinc-800/80 bg-zinc-900/40 backdrop-blur-xl p-8 shadow-2xl space-y-6">
          
          {/* Logo & Header */}
          <div className="text-center space-y-2">
            <div className="inline-flex w-14 h-14 rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600 items-center justify-center text-3xl shadow-xl shadow-indigo-500/10 animate-bounce" style={{ animationDuration: "3s" }}>
              📊
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-white mt-4">Welcome to AnalystAI</h1>
            <p className="text-xs text-zinc-500 max-w-xs mx-auto">
              Your autonomous AI data analyst that processes raw datasets into board-ready boardrooms
            </p>
          </div>

          {/* Form Tabs */}
          <div className="flex bg-zinc-950/80 border border-zinc-800/60 p-1 rounded-xl">
            <button
              onClick={() => {
                setIsLogin(true);
                setError(null);
              }}
              className={`flex-1 py-2 text-xs font-semibold rounded-lg transition-all ${
                isLogin
                  ? "bg-zinc-800 text-white shadow-sm"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              Sign In
            </button>
            <button
              onClick={() => {
                setIsLogin(false);
                setError(null);
              }}
              className={`flex-1 py-2 text-xs font-semibold rounded-lg transition-all ${
                !isLogin
                  ? "bg-zinc-800 text-white shadow-sm"
                  : "text-zinc-500 hover:text-zinc-300"
              }`}
            >
              Register
            </button>
          </div>

          {/* Error Notice */}
          {error && (
            <div className="text-xs bg-red-500/10 border border-red-500/20 rounded-xl px-4 py-3 text-red-400 text-center font-medium">
              ⚠️ {error}
            </div>
          )}

          {notice && (
            <div role="status" className="text-xs bg-emerald-500/10 border border-emerald-500/20 rounded-xl px-4 py-3 text-emerald-400 text-center font-medium">
              {notice}
            </div>
          )}

          {/* Form */}
          <form onSubmit={handleSubmit} className="space-y-4">
            {!isLogin && (
              <div className="space-y-1.5">
                <label className="text-xs font-medium text-zinc-400">Full Name</label>
                <input
                  type="text"
                  placeholder="John Doe"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="w-full bg-zinc-950/50 border border-zinc-800/80 focus:border-indigo-500/60 rounded-xl px-4 py-3 text-sm text-zinc-200 placeholder:text-zinc-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/10 transition-all"
                />
              </div>
            )}

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-zinc-400">Email Address</label>
              <input
                type="email"
                placeholder="you@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full bg-zinc-950/50 border border-zinc-800/80 focus:border-indigo-500/60 rounded-xl px-4 py-3 text-sm text-zinc-200 placeholder:text-zinc-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/10 transition-all"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-zinc-400">Password</label>
              <input
                type="password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full bg-zinc-950/50 border border-zinc-800/80 focus:border-indigo-500/60 rounded-xl px-4 py-3 text-sm text-zinc-200 placeholder:text-zinc-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/10 transition-all"
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full py-3 bg-gradient-to-r from-indigo-600 to-purple-600 hover:from-indigo-500 hover:to-purple-500 text-white text-sm font-semibold rounded-xl transition-all shadow-lg shadow-indigo-500/20 hover:shadow-indigo-500/35 active:scale-95 disabled:opacity-50"
            >
              {loading ? "Please wait..." : isLogin ? "Sign In →" : "Create Account →"}
            </button>
          </form>

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div className="h-[1px] flex-1 bg-zinc-800/80" />
            <span className="text-[10px] text-zinc-600 font-medium uppercase tracking-wider">Or continue with</span>
            <div className="h-[1px] flex-1 bg-zinc-800/80" />
          </div>

          {/* OAuth Buttons */}
          <button
            onClick={loginWithGoogle}
            className="w-full flex items-center justify-center gap-3 py-3 bg-zinc-950/50 border border-zinc-800/80 hover:border-zinc-700/80 text-zinc-300 hover:text-white text-xs font-semibold rounded-xl transition-all active:scale-95"
          >
            {/* Google Icon SVG */}
            <svg className="w-4 h-4" viewBox="0 0 24 24">
              <path
                fill="currentColor"
                d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
              />
              <path
                fill="#34A853"
                d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
              />
              <path
                fill="#FBBC05"
                d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
              />
              <path
                fill="#EA4335"
                d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
              />
            </svg>
            Sign in with Google (OAuth2)
          </button>
        </div>
      </div>
    </div>
  );
}
