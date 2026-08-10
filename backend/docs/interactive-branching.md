# Interactive branching narrative

`Timeline.settings_json.interactive_graph` uses graph schema v1: `nodes` identify one proxy-playable source range and `edges` represent choice text, normalised Canvas position, and a target node. The existing linear timeline remains untouched for conventional rendering.

Public playback uses `GET /api/v1/interactive/timelines/{timeline_id}/manifest`; only `published: true` graphs are exposed, and every node must have a proxy. The player records pseudonymous session and append-only node/choice events. Creator analytics is available from `GET /api/v1/timelines/{timeline_id}/interactive-analytics?user_id=...` and returns Sankey-ready nodes/links.

For production, replace the current project `user_id` ownership convention with authenticated principal extraction, rate-limit public session/event endpoints, and serve proxy URLs behind a CDN with short-lived signed cookies rather than browser-visible presigned URLs for premium content.
