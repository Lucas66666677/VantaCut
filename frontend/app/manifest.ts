import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return { name: "VantaCut", short_name: "VantaCut", description: "AI-assisted video editing", start_url: "/", display: "standalone", background_color: "#09090f", theme_color: "#09090f" };
}
