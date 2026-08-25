import yt_dlp

url = "https://www.youtube.com/shorts/xbXjL0zB08w"

ydl_opts = {
    "outtmpl": "%(title)s.%(ext)s",
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    ydl.download([url])