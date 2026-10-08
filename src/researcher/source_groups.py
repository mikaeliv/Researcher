"""Независимые площадки: категории одной платформы не увеличивают diversity."""

from urllib.parse import urlsplit

SOURCE_GROUPS = {
    "Hacker News / Ask HN": "hackernews",
    "Home Assistant / Feature Requests": "homeassistant",
    "Lemmy / Productivity": "lemmy",
    "Lemmy / Selfhosted": "lemmy",
    "Lemmy / Apps": "lemmy",
    "Stack Exchange / Personal Finance": "stackexchange",
    "Stack Exchange / Home Improvement": "stackexchange",
    "TrueNAS Community": "truenas",
    "Nextcloud Community": "nextcloud",
    "Proxmox Support Forum": "proxmox",
}
PLATFORM_GROUPS = {"lemmy", "hackernews", "stackexchange", "reddit", "youtube", "appstore"}
HOST_GROUPS = {
    "community.home-assistant.io": "homeassistant",
    "forums.truenas.com": "truenas",
    "help.nextcloud.com": "nextcloud",
    "forum.proxmox.com": "proxmox",
}


def source_group_key(kind: str, name: str, config: dict) -> str:
    if name in SOURCE_GROUPS:
        return SOURCE_GROUPS[name]
    if kind in PLATFORM_GROUPS:
        return kind
    for key in ("feed_url", "base_url", "url"):
        host = urlsplit(config.get(key, "")).hostname
        if host:
            host = host.removeprefix("www.")
            return HOST_GROUPS.get(host, host)
    # shortcut: unknown feed/forum without URL shares its kind, add an explicit mapping if needed.
    return kind


def source_group_default(context) -> str:
    data = context.get_current_parameters()
    return source_group_key(data["kind"], data["name"], data.get("config") or {})
