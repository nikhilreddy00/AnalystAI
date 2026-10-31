import { Suspense } from "react";
import DatasetsView from "@/components/DatasetsView";

export const metadata = { title: "Datasets — AnalystAI" };

// DatasetsView reads ?d=<id> (useSearchParams): it renders on the client.
export default function DatasetsPage() {
  return (
    <Suspense>
      <DatasetsView />
    </Suspense>
  );
}
