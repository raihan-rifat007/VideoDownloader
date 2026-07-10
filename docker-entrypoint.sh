#!/bin/sh
# Keep yt-dlp fresh on container start because site extractors change frequently.
# Installs into the reclip user's Python user site, which core invokes as a module.
# Skip with RECLIP_NO_UPDATE=1.
if [ -z "$RECLIP_NO_UPDATE" ]; then
    echo "Updating yt-dlp..."
    pip install --user --no-cache-dir -q -U yt-dlp || \
        echo "  (couldn't update yt-dlp — continuing with the installed version)"
fi

exec "$@"
