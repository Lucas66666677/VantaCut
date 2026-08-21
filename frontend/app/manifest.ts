import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return { name: "VantaCut by Lucirel", short_name: "VantaCut", description: "AI-assisted video editing", start_url: "/", display: "standalone", background_color: "#0D0F13", theme_color: "#15181E" };
}
