"use client";

import { useEffect } from "react";

export function DeepLinkRedirect() {
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (!params.has("story") && !params.has("run")) return;
    window.location.replace(`/demo${window.location.search}${window.location.hash}`);
  }, []);

  return null;
}
