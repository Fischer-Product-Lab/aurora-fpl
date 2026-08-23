import { redirect } from "next/navigation";
import { DeepLinkRedirect } from "./deep-link-redirect";
import { getLandingShowcase } from "./landing-data";
import { LandingPage } from "./landing";

type SearchValue = string | string[] | undefined;

function firstValue(value: SearchValue) {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

export default async function Home({
  searchParams,
}: {
  searchParams?: Promise<Record<string, SearchValue>>;
}) {
  const params = searchParams ? await searchParams : {};
  const story = firstValue(params.story);
  const run = firstValue(params.run);

  if (story || run) {
    const query = new URLSearchParams();
    if (story) query.set("story", story);
    if (run) query.set("run", run);
    redirect(`/demo?${query.toString()}`);
  }

  return (
    <>
      <DeepLinkRedirect />
      <LandingPage showcase={getLandingShowcase()} />
    </>
  );
}
