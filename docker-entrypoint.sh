#!/bin/sh
# Keep this script LF-only; Debian /bin/sh does not accept CRLF shell syntax.
# Keep yt-dlp fresh on container start — sites (Instagram, Facebook, etc.) break
# its extractors frequently, and the usual fix is simply updating yt-dlp.
# Installs into the reclip user's ~/.local (first on PATH). Skip with RECLIP_NO_UPDATE=1.
YT_DLP_REQUIREMENT="yt-dlp>=2026.8.19"

if [ -z "$RECLIP_NO_UPDATE" ]; then
    echo "Ensuring $YT_DLP_REQUIREMENT..."
    pip install --user --no-cache-dir -q -U "$YT_DLP_REQUIREMENT" || \
        echo "  (couldn't update yt-dlp — continuing with the installed version)"
fi

echo "Using yt-dlp $(yt-dlp --version 2>/dev/null || echo unavailable)"
exec "$@"
