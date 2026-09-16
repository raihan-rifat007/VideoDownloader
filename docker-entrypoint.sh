if [ -z "$VIDEODOWNLOADER_NO_UPDATE" ]; then
    echo "Updating yt-dlp..."
    pip install --user --no-cache-dir -q -U yt-dlp || \
        echo "  (couldn't update yt-dlp — continuing with the installed version)"
fi

exec "$@"
