import { Suspense } from "react";
import ChatInterface from "@/components/ChatInterface";

export const metadata = { title: "Chat — AnalystAI" };

// ChatInterface reads the thread from the URL (useSearchParams): it renders on the client.
export default function ChatPage() {
  return (
    <Suspense>
      <ChatInterface />
    </Suspense>
  );
}
