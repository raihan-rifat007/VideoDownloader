#!/bin/sh
set -u

# Extractors and their JS challenge solver age faster than the pinned ASR stack.
# Refresh only those two components, fail soft, and leave CUDA/model packages fixed.
if [ "${RECLIP_NO_UPDATE:-0}" != "1" ]; then
    echo "Checking yt-dlp extractor components for updates..."
    uv pip install --python "${VIRTUAL_ENV}/bin/python" --no-cache -q -U \
        "yt-dlp[default]" yt-dlp-ejs || \
        echo "yt-dlp update failed; continuing with the image-pinned versions"
fi

exec "$@"
